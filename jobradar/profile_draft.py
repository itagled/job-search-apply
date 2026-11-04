"""Make the target profile from what the user gives: a job posting they like or their ideal role in words.

    jobradar profile from-url <link>      # LinkedIn, Ashby, Greenhouse, Lever, Workable or any page
    jobradar profile from-text [file|-]   # a posting or a description, as text

The posting is read with the repo's own readers (no browser needed), and an LLM proposes the role,
search keywords, the ideal employer and a description of the ideal role. The LLM only returns words;
this module turns them into a valid profile and keeps the original text at the end. Everything else
follows from this file: the keywords are what every job board is searched for and what titles are
judged against, and the description is what each posting is scored against.
It is saved as ``profile/target.md``, or as ``profile/target.draft.md`` if there is one already.
"""

from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobradar import config, http_client, llm, profile
from jobradar.text import strip_html

_LINKEDIN_ID = re.compile(r"linkedin\.com/jobs/view/(?:[^/\s?]*-)?(\d{6,})|currentJobId=(\d{6,})")

MIN_TEXT = 30  # a sentence or two is enough to describe a role

SYSTEM_PROMPT = """You help a job seeker set up a job-search tool. From a REFERENCE JOB POSTING they like, or a
description of their ideal role in their own words (and their optional notes), propose their search profile.
Generalize: describe the KIND of role, not one vacancy. Do not include personal data of people named in the text.
Answer ONLY with a JSON object with the keys:
"role": short name of the role (e.g. "Data Analyst"),
"keywords": 6 to 15 search phrases job boards would match for this kind of role (titles and close variants;
  in the language of the text and in English). They decide what is searched and which job titles count as the
  role, and each one costs a search,
"niche_sources": the names of these specialized job boards, only those that publish this kind of role: {niche},
"contact_functions": 2 to 5 of these LinkedIn functions, the people who hire for this role: {functions},
"company_ideal": 1 or 2 sentences on the kind of employer (industry, stage, size, business model),
"description": a Markdown description of the IDEAL ROLE, in the language of the text, with the sections "# Ideal role: <role>",
  "## What the role is", "## Core responsibilities", "## Typical stack", "## It is NOT". Keep it general: leave
  out what only this employer has (its industry, product, niche tools or years asked) unless the notes ask for it.
  Do not write the candidate's preferences: they fill those in."""


def _niche_sources() -> dict[str, str]:
    """Job boards of one field: name -> who they are for."""
    from jobradar.sources.base import REGISTRY
    from jobradar.sources.connectors import load_all

    load_all()
    return {name: cls.niche for name, cls in REGISTRY.items() if cls.niche}


def _system_prompt() -> str:
    from jobradar.companies.contacts_apify import LINKEDIN_FUNCTIONS

    return SYSTEM_PROMPT.format(niche=json.dumps(_niche_sources()), functions=", ".join(LINKEDIN_FUNCTIONS))


@dataclass
class Posting:
    title: str = ""
    company: str = ""
    location: str = ""
    description: str = ""
    url: str = ""


class DraftError(RuntimeError):
    """The posting could not be read or the draft is not usable; the message says what to do."""


def fetch(url: str) -> Posting:
    """Read a job posting from its link with the repo's readers."""
    m = _LINKEDIN_ID.search(url)
    if m:
        from jobradar.sources import linkedin_public

        page = linkedin_public.detail_page(m.group(1) or m.group(2))
        if page:
            return Posting(
                page.get("title") or "",
                page.get("companyName") or "",
                page.get("location") or "",
                page.get("description") or "",
                url,
            )
    else:
        from jobradar.sources import ats

        found = ats.detail(url, read_page=True)
        if found and found.description:
            return Posting(found.title or "", found.company or "", found.location or "", found.description, url)
        resp = http_client.get(url, timeout=40)
        text = strip_html(resp.text) if resp.ok else ""
        if len(text) > 400:
            return Posting(description=text[:12000], url=url)
    raise DraftError(
        "could not read the posting (it may need a login): paste its text with `jobradar profile from-text`"
    )


def _phrases(values: Any) -> list[str]:
    return [str(v).strip() for v in values or [] if str(v).strip()]


def _search_place(location: str) -> tuple[str, bool]:
    """Where to search and if only remote jobs: the country of the posting, or remote jobs anywhere without one."""
    country = location.split(",")[-1].strip()
    if not country or country.lower() in ("remote", "remoto", "worldwide", "anywhere"):
        return "Worldwide", True
    return country, False


def render(data: dict[str, Any], posting: Posting) -> str:
    """The profile text (TOML header + description) from the LLM's answer."""
    role = str(data.get("role") or "").strip() or posting.title or "the target role"
    keywords = _phrases(data.get("keywords")) or [role]
    place, remote = _search_place(posting.location)
    from jobradar.companies.contacts_apify import LINKEDIN_FUNCTIONS

    niche = [name for name in _phrases(data.get("niche_sources")) if name in _niche_sources()]
    functions = [name for name in _phrases(data.get("contact_functions")) if name in LINKEDIN_FUNCTIONS]
    lines = [
        "+++",
        "# Made from a job posting or a description by `jobradar profile`. The keywords decide what is searched and",
        "# the text below decides how postings are scored. Set [location] to where you live, and add to the text",
        '# "## The candidate" (background, experience) and "## Preferences" (work mode, seniority, industries to',
        "# avoid). `jobradar profile check` validates this file. All options: profile/target.example.md",
        f"role = {json.dumps(role, ensure_ascii=False)}",
        "",
        "[search]",
        f"keywords = {json.dumps(keywords, ensure_ascii=False)}",
        f"locations = [{{name = {json.dumps(place, ensure_ascii=False)}, remote = {str(remote).lower()}}}]  # LinkedIn",
        *([f"niche_sources = {json.dumps(niche)}  # job boards of one field"] if niche else []),
        "",
        "[location]",
        'preset = "any"  # where you live and which jobs you can take: see profile/target.example.md',
        "",
        "[companies]",
        f"ideal = {json.dumps(str(data.get('company_ideal') or ''), ensure_ascii=False)}",
        *([f"contact_functions = {json.dumps(functions)}  # who to find at each company"] if functions else []),
        "+++",
        "",
        str(data.get("description") or f"# Ideal role: {role}").strip(),
        "",
        profile.REFERENCE_HEADING,
        posting.url or "(given as text)",
        "",
        posting.description.strip(),
        "",
    ]
    text = "\n".join(lines)
    profile.parse(text)  # the draft must be a valid profile
    return text


def draft(posting: Posting, *, notes: str = "", model: str | None = None) -> str:
    if len(posting.description.strip()) < MIN_TEXT:
        raise DraftError("the text is too short: paste a job posting or describe the role in a sentence or two")
    user = (
        f"REFERENCE JOB POSTING OR DESCRIPTION OF THE IDEAL ROLE\nTitle: {posting.title or '(unknown)'}\nCompany: {posting.company or '(unknown)'}\n"
        f"Location: {posting.location or '(unknown)'}\nDescription:\n{posting.description[:12000]}\n\n"
        f"NOTES FROM THE CANDIDATE: {notes or '(none)'}"
    )
    data = llm.chat_json(_system_prompt(), user, model=model or config.get("FIT_MODEL"), temperature=0).data
    try:
        return render(data if isinstance(data, dict) else {}, posting)
    except profile.ProfileError as exc:
        raise DraftError(f"the LLM's draft is not a valid profile ({exc}); try again or write it by hand") from None


def write(text: str, out: Path | None = None) -> Path:
    """Save the profile: the active one if there is none yet, otherwise a draft next to it."""
    active = profile.path()
    out = out or (active.with_name("target.draft.md") if active.exists() else active)
    if not out.is_absolute():
        out = config.ROOT / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(text, encoding="utf-8", newline="\n")
    return out
