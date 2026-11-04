"""LinkedIn posts where someone announces they are hiring (paid: Apify + a cheap LLM).

1. Apify (``harvestapi/linkedin-post-search``) searches for posts with the profile's phrases (default
   ``hiring "<keyword>"``). From the raw data come the lists of reactions and comments (third-party data).
2. A cheap LLM decides whether the post offers a specific, relevant role the candidate can take
   from where they live, and extracts role, company, and location. That decision replaces the title filter.
3. If the post links to a LinkedIn job posting, its public page is used; otherwise, the posting is built from the post.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any

from jobradar import apify, config, llm, profile
from jobradar.dates import parse_datetime
from jobradar.sources import linkedin_public
from jobradar.sources.base import Connector, Posting, register

_PROMPT = """You classify LinkedIn posts for a job seeker based in {home}. Answer ONLY with a JSON object.
Decide if the post announces a concrete OPEN POSITION that a company is hiring for (the author hires for their company
or a recruiter posts a specific role). NOT job offers: people looking for work, generic "we are growing" posts, courses,
events, lists of tips, ads for recruiting services, "DM me for opportunities" without a role.
{relevant}
eligible: {eligible}
JSON keys: is_job_offer (bool), is_relevant (bool), eligible (bool|null), role_title (string|null, the role as written),
company (string|null), location (string|null, as written), remote (bool|null), apply_link (string|null)."""


def system_prompt() -> str:
    """The classifier prompt for the profile: its role and where the candidate lives."""
    prof = profile.load()
    home = prof.location.home_name or "their home country"
    relevant = prof.search.for_source(
        "hiring_posts_relevant",
        f"Relevant roles (is_relevant=true): roles that match the target role ({prof.role}):\n"
        f"{prof.description[:1500]}",
    )
    eligible = prof.search.for_source(
        "hiring_posts_eligible",
        f"true if someone living in {home} could take it (based in {home}, or remote open to people there); false "
        f"if it is on-site or hybrid elsewhere, or remote restricted to another country or region; null if unknown.",
    )
    return _PROMPT.format(home=home, relevant=relevant, eligible=eligible)


_LINKEDIN_POSTING = re.compile(r"linkedin\.com/jobs/view/(?:[^/\s]*-)?(\d{6,})")


def _is_relevant(r: dict) -> bool:
    return bool(r.get("is_job_offer") and r.get("is_relevant")) and r.get("eligible") is not False


def classify(post: dict) -> dict | None:
    """LLM decision about a post (or ``None`` if it failed). If it responds with a list, the first one that works."""
    author = post.get("author") if isinstance(post.get("author"), dict) else {}
    user = (
        f"Author: {author.get('name')} | {author.get('info') or author.get('headline') or ''}\n"
        f"Post:\n{(post.get('content') or '')[:6000]}"
    )
    try:
        data: Any = llm.chat_json(system_prompt(), user, model=config.get("POSTS_MODEL") or "", timeout=90).data
    except llm.OutOfCreditsError:
        raise
    except llm.LLMError:
        return None
    if isinstance(data, dict) and isinstance(data.get("jobs"), list):
        data = data["jobs"]
    if isinstance(data, list):
        dicts = [x for x in data if isinstance(x, dict)]
        return next((x for x in dicts if _is_relevant(x)), dicts[0] if dicts else None)
    return data


@register
class HiringPosts(Connector):
    name = "linkedin_hiring_post"
    description = "LinkedIn posts announcing a role (Apify + a cheap LLM classifier)"
    paid = True
    third_party = True  # the post includes who is posting
    applies_filters = False  # the LLM already decided if it is useful
    ACTOR = "harvestapi/linkedin-post-search"
    USD_CAP = 1.8
    POSTS_PER_PHRASE = 22

    def search(self, *, days: int) -> Iterable[Posting]:
        window = "24h" if days <= 1 else "week" if days <= 7 else "month"
        posts: dict[str, dict] = {}
        search = profile.load().search
        global_queries = search.for_source("hiring_posts_global", [f'hiring "{k}"' for k in search.keywords])
        for queries in (global_queries, search.for_source("hiring_posts_local", [])):
            if not queries:
                continue
            payload = {
                "searchQueries": queries,
                "maxPosts": self.POSTS_PER_PHRASE,
                "postedLimit": window,
                "sortBy": "date",
            }
            run = apify.run(
                self.ACTOR,
                payload,
                max_total_charge_usd=round(self.USD_CAP / 2, 3),
                accept_partial=True,
                raw_kind=f"sources/{self.name}",
                third_party=True,
            )
            for post in run.items:
                pid = str(post.get("id") or post.get("entityId") or "")
                if pid and (post.get("content") or "").strip():
                    posts.setdefault(pid, apify.minimize(post))
        for pid, post in posts.items():
            decision = classify(post)
            if decision and _is_relevant(decision):
                yield self._posting(pid, post, decision)

    @staticmethod
    def _posting(pid: str, post: dict, r: dict) -> Posting:
        author = post.get("author") if isinstance(post.get("author"), dict) else {}
        posted = post.get("postedAt")
        posted = parse_datetime(posted.get("date") if isinstance(posted, dict) else posted)
        extra = {
            "post_url": post.get("linkedinUrl"),
            "author_name": author.get("name"),
            "author_headline": author.get("info") or author.get("headline"),
            "author_linkedin_url": author.get("linkedinUrl"),
            "classify": r,
        }
        link = _LINKEDIN_POSTING.search(f"{r.get('apply_link') or ''} {post.get('content') or ''}")
        detail_page = linkedin_public.detail_page(link.group(1)) if link else None
        if detail_page:  # the post links to a LinkedIn job posting: its public page is used
            return Posting(
                id=link.group(1),
                title=detail_page.get("title") or r.get("role_title") or "",
                company=detail_page.get("companyName") or r.get("company"),
                location=detail_page.get("location"),
                url=f"https://www.linkedin.com/jobs/view/{link.group(1)}/",
                description=detail_page["description"],
                posted_at=posted,
                extra=extra,
            )
        return Posting(
            id=f"urn:li:activity:{pid}",
            title=r.get("role_title") or "",
            company=r.get("company"),
            location=r.get("location") or ("Remote" if r.get("remote") else None),
            url=post.get("linkedinUrl"),
            apply_url=r.get("apply_link"),
            description=post.get("content"),
            posted_at=posted,
            remote=r.get("remote"),
            extra=extra,
        )
