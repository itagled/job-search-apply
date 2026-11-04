"""Retrieve a company description via Google Search on Apify (cheap).

Uses the actor ``scraperlink/google-search-results-serp-scraper`` (~$0.0005/search).
Cheap replacement for Serper. Returns a description text from the
snippets: prefers the one from the company's own domain.
"""

from __future__ import annotations

import logging
import re

from jobradar import apify, config, profile
from jobradar.companies.normalize import clean_optional
from jobradar.text import host

log = logging.getLogger(__name__)
_LINKEDIN_COMPANY = re.compile(r"https?://[a-z]{0,3}\.?linkedin\.com/company/[^\s?#\"']+", re.IGNORECASE)


def _flatten_results(items: list[dict]) -> list[dict]:
    """Flatten the nested ``results[]`` of each SERP page."""
    out: list[dict] = []
    for it in items if isinstance(items, list) else []:
        res = it.get("results") if isinstance(it, dict) else None
        if isinstance(res, list):
            out.extend(r for r in res if isinstance(r, dict))
    return out


def _search(keyword: str, *, timeout: int) -> list[dict]:
    """Flat results from a Google search (via Apify), in the profile's country. Soft-fail: ``[]``."""
    if not apify.token():
        return []
    code = profile.load().companies.country_code
    try:
        items = apify.run_sync(
            config.get("APIFY_SERP_ACTOR") or "",
            {"keyword": keyword, "limit": "10", **({"country": code} if code else {})},
            timeout_secs=max(30, timeout - 40),
            raw_kind="serp",
        )
    except apify.ApifyError as exc:
        log.warning("search %r: %s", keyword, exc)
        return []
    return _flatten_results(items)


def fetch_description_serp(name: str, domain: str | None, *, timeout: int = 130) -> str | None:
    """Search the company on Google (Apify) and build a description from the snippets.

    Prefers the snippet whose ``url`` is from the company's own domain; otherwise,
    concatenates the top 2–3 snippets. Returns ``None`` if nothing useful.
    """
    if not name:
        return None
    # With domain: precise query. Without domain: name + country to disambiguate.
    keyword = f"{name} {domain or profile.load().companies.country}".strip()
    results = _search(keyword, timeout=timeout)
    if not results:
        return None

    own_snippet: str | None = None
    snippets: list[str] = []
    dom = (domain or "").lower()
    for r in results:
        desc = clean_optional(r.get("description") or r.get("snippet"))
        if not desc:
            continue
        snippets.append(desc)
        if dom and own_snippet is None and dom in host(r.get("url") or r.get("link")):
            own_snippet = desc

    if not snippets:
        return None
    if own_snippet:
        extras = [s for s in snippets if s != own_snippet][:2]
        text = " | ".join([own_snippet, *extras])
    else:
        text = " | ".join(snippets[:3])
    return text[:1500] or None


def fetch_company_linkedin_url(name: str, domain: str | None, *, timeout: int = 130) -> str | None:
    """Search Google (Apify) for the company's LinkedIn URL.

    Query ``{name} site:linkedin.com/company``; returns the first URL of
    ``linkedin.com/company/...`` found (without query/fragment), or ``None``.
    """
    if not name:
        return None
    for r in _search(f"{name} site:linkedin.com/company", timeout=timeout):
        url = r.get("url") or r.get("link") or ""
        m = _LINKEDIN_COMPANY.search(str(url)) or _LINKEDIN_COMPANY.search(str(r.get("description") or ""))
        if m:
            found = m.group(0).rstrip("/")
            return found
    return None
