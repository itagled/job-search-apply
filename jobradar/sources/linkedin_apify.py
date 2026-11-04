"""LinkedIn Jobs scraper via Apify (``valig/linkedin-jobs-scraper``).

Chosen over ``bebity/linkedin-jobs-scraper`` (tested 2026-09-16): it returns the
same listings with full JD + recruiter for ~US$0.0004 per listing (bebity
~US$0.0015 and adds company data that is not needed here).

One run = one ``keywords``. ``titleInclude`` filters server-side by title, so
only listings matching the profile are paid for.
"""

from __future__ import annotations

from typing import Any

from jobradar import apify, config, profile
from jobradar.dates import parse_datetime

DEFAULT_ACTOR = "valig/linkedin-jobs-scraper"
SOURCE = "linkedin_jobs_valig"

# Actor's datePosted (seconds): last month / week / 24 h.
DATE_POSTED = {"month": "r2592000", "week": "r604800", "24h": "r86400", "any": ""}


def actor_id() -> str:
    return config.get("APIFY_LINKEDIN_JOBS_ACTOR") or DEFAULT_ACTOR


def build_payload(
    keyword: str,
    *,
    location: str,
    date_posted: str = "month",
    limit: int = 200,
    title_filter: bool = True,
    remote: bool = False,
    title_include: list[str] | None = None,
) -> dict[str, Any]:
    """Actor input for a search. The actor keeps only titles with all the words of one phrase of
    ``title_include`` (default: the profile's ``linkedin_title_include``, else its keywords)."""
    payload: dict[str, Any] = {
        "keywords": keyword,
        "location": location,
        "datePosted": DATE_POSTED[date_posted],
        "limit": max(1, int(limit)),
    }
    if title_filter:
        search = profile.load().search
        payload["titleInclude"] = list(
            title_include or search.for_source("linkedin_title_include", [k.lower() for k in search.keywords])
        )
    if remote:
        # LinkedIn "Remote" filter (f_WT: 1 on-site, 2 remote, 3 hybrid).
        payload["urlParam"] = [{"key": "f_WT", "value": "2"}]
    return payload


def fetch_jobs(payload: dict[str, Any], *, max_charge_usd: float | None = None) -> apify.ApifyRun:
    """Runs the actor with ``payload``: the run brings the listings, status, and actual cost.

    The raw data is saved per run, as third-party data (includes recruiter name and profile).
    """
    # The actor's default (300 s) is not enough when the title filter discards a lot;
    # if it still cuts off, the already-charged listings are saved.
    return apify.run(
        actor_id(),
        payload,
        timeout_secs=1500.0,
        max_total_charge_usd=max_charge_usd,
        run_timeout_secs=1200,
        accept_partial=True,
        raw_kind="linkedin_jobs_valig",
        third_party=True,
    )


def job_fields(item: dict[str, Any]) -> dict[str, Any] | None:
    """Raw valig listing → ``job_post`` columns. None if it has no id."""
    job_id = str(item.get("id") or "").strip()
    if not job_id:
        return None
    description = str(item.get("description") or "").strip() or None
    return {
        "external_id": job_id,
        "title": str(item.get("title") or "").strip() or None,
        "company": str(item.get("companyName") or "").strip() or None,
        "location": str(item.get("location") or "").strip() or None,
        "post_url": str(item.get("url") or "").strip() or None,
        "posted_at": parse_datetime(item.get("postedDate")),
        "description": description,
    }
