"""LinkedIn Jobs search via the public endpoint (no session, no cost).

It is the same thing the Apify actor does underneath, but direct: the search
returns HTML cards (id, title, company, location, date) and each job posting's
detail page brings the full description.

Responsible use: a 1.2 s pause between requests to LinkedIn and retries with backoff on
429/5xx (via ``jobradar.http_client``).
"""

from __future__ import annotations

import html as _html
import re
from typing import Any

from jobradar import http_client

SEARCH_URL = "https://www.linkedin.com/jobs-guest/jobs/api/seeMoreJobPostings/search"
POSTING_URL = "https://www.linkedin.com/jobs-guest/jobs/api/jobPosting/{job_id}"
PAGE_SIZE = 25
PAUSE = 1.2  # seconds between requests to LinkedIn
_UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/120.0.0.0 Safari/537.36"
DATE_POSTED = {"month": "r2592000", "week": "r604800", "24h": "r86400"}

_RE_CARD = re.compile(r"<li\b.*?</li>", re.S | re.I)
_RE_URN = re.compile(r"urn:li:jobPosting:(\d{6,})")
_RE_CARD_TITLE = re.compile(r'class="base-search-card__title"[^>]*>(.*?)</h3>', re.S | re.I)
_RE_CARD_COMPANY = re.compile(r'class="base-search-card__subtitle"[^>]*>(.*?)</h4>', re.S | re.I)
_RE_CARD_LOCATION = re.compile(r'class="job-search-card__location"[^>]*>(.*?)</span>', re.S | re.I)
_RE_CARD_DATE = re.compile(r'<time[^>]*datetime="([^"]+)"', re.I)
_RE_CARD_LINK = re.compile(r'class="base-card__full-link[^"]*"[^>]*href="([^"?]+)', re.S | re.I)

_RE_TITLE = re.compile(r'<h[12345][^>]*class="[^"]*topcard__title[^"]*"[^>]*>(.*?)</h[12345]>', re.S | re.I)
_RE_COMPANY = re.compile(r'<a[^>]*class="[^"]*topcard__org-name-link[^"]*"[^>]*>(.*?)</a>', re.S | re.I)
_RE_COMPANY_URL = re.compile(r'<a[^>]*class="[^"]*topcard__org-name-link[^"]*"[^>]*href="([^"?]+)', re.S | re.I)
_RE_LOCATION = re.compile(r'<span[^>]*class="[^"]*topcard__flavor--bullet[^"]*"[^>]*>(.*?)</span>', re.S | re.I)
_RE_MARKUP_START = re.compile(r'<div class="[^"]*show-more-less-html__markup[^"]*"[^>]*>', re.I)
_RE_CRITERIA = re.compile(
    r"<h3[^>]*description__job-criteria-subheader[^>]*>(.*?)</h3>\s*<span[^>]*>(.*?)</span>", re.S | re.I
)
_RE_APPLICANTS = re.compile(r'class="[^"]*num-applicants__caption[^"]*"[^>]*>(.*?)<', re.S | re.I)


def _text(snippet: str | None) -> str | None:
    if not snippet:
        return None
    clean = _html.unescape(re.sub(r"<[^>]+>", "", snippet))
    clean = re.sub(r"\s+", " ", clean).strip()
    return clean or None


def _get(url: str, *, params: dict | None = None) -> str | None:
    """GET with pause and retries on 429/5xx. ``None`` if the resource does not exist or fails."""
    try:
        resp = http_client.get(
            url, params=params, headers={"User-Agent": _UA}, min_interval=PAUSE, timeout=30, retries=3, backoff=3.0
        )
    except Exception:  # noqa: BLE001 - the network failed on all attempts
        return None
    return resp.text if resp.status_code == 200 else None


def search_cards(
    keywords: str,
    *,
    location: str,
    date_posted: str = "month",
    remote: bool = False,
    max_results: int = 100,
) -> list[dict[str, Any]]:
    """Cards from the public search (paginated by 25)."""
    cards: list[dict[str, Any]] = []
    start = 0
    while start < max_results:
        params = {"keywords": keywords, "location": location, "f_TPR": DATE_POSTED[date_posted], "start": start}
        if remote:
            params["f_WT"] = "2"
        page = _get(SEARCH_URL, params=params)
        if not page:
            break
        found = 0
        for li in _RE_CARD.findall(page):
            urn = _RE_URN.search(li)
            if not urn:
                continue
            found += 1
            link = _RE_CARD_LINK.search(li)
            date = _RE_CARD_DATE.search(li)
            cards.append(
                {
                    "id": urn.group(1),
                    "title": _text((_RE_CARD_TITLE.search(li) or [None, None])[1]),
                    "companyName": _text((_RE_CARD_COMPANY.search(li) or [None, None])[1]),
                    "location": _text((_RE_CARD_LOCATION.search(li) or [None, None])[1]),
                    "postedDate": date.group(1) if date else None,
                    "url": link.group(1) if link else f"https://www.linkedin.com/jobs/view/{urn.group(1)}/",
                }
            )
        # The page size varies (10 or 25): advance by what is received and stop at the empty page.
        if found == 0:
            break
        start += found
    return cards


def _description_body(page: str) -> str:
    start = _RE_MARKUP_START.search(page)
    if not start:
        return ""
    pos, depth = start.end(), 1
    for tag in re.finditer(r"<(/?)div\b", page[pos:], re.I):
        depth += -1 if tag.group(1) else 1
        if depth == 0:
            return page[pos : pos + tag.start()]
    return page[pos:]


def _to_plain_text(body: str) -> str:
    text = re.sub(r"<br\s*/?>", "\n", body, flags=re.I)
    text = re.sub(r"</(p|li|ul|ol|div|h[1-6])>", "\n", text, flags=re.I)
    text = re.sub(r"<li[^>]*>", "- ", text, flags=re.I)
    text = _html.unescape(re.sub(r"<[^>]+>", "", text))
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = "\n".join(line.strip() for line in text.split("\n"))
    return re.sub(r"\n{3,}", "\n\n", text).strip()


def detail_page(job_id: str) -> dict[str, Any] | None:
    """Public job posting detail (description + criteria). None if it does not exist or has no description."""
    page = _get(POSTING_URL.format(job_id=job_id))
    if not page:
        return None
    body = _description_body(page)
    description = _to_plain_text(body)
    if not description:
        return None
    criteria = {_text(k): _text(v) for k, v in _RE_CRITERIA.findall(page)}
    company_url = _RE_COMPANY_URL.search(page)
    return {
        "title": _text((_RE_TITLE.search(page) or [None, None])[1]),
        "companyName": _text((_RE_COMPANY.search(page) or [None, None])[1]),
        "companyUrl": company_url.group(1) if company_url else None,
        "location": _text((_RE_LOCATION.search(page) or [None, None])[1]),
        "description": description,
        "descriptionHtml": body.strip(),
        "criteria": criteria,
        "applicationsCount": _text((_RE_APPLICANTS.search(page) or [None, None])[1]),
    }
