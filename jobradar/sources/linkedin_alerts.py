"""Parsing of LinkedIn job alert emails → job posts.

Extracts, from the email HTML, the links to vacancies ``.../jobs/view/<id>`` and their
title (anchor text). Company/location remain best-effort: LinkedIn
places them in sibling nodes whose layout varies by language/template, so in the
v1 they are left as None and refined after seeing a real email in the dry-run.
"""

from __future__ import annotations

import re
from dataclasses import dataclass
from html.parser import HTMLParser
from urllib.parse import unquote

# Capture the numeric vacancy id in any LinkedIn URL variant
# (with or without the /comm/ prefix from emails, and with tracking querystring).
JOB_ID_RE = re.compile(r"linkedin\.com/(?:comm/)?jobs/view/(\d+)", re.IGNORECASE)

# Common senders of LinkedIn job alerts.
DEFAULT_ALERT_SENDERS = (
    "jobalerts-noreply@linkedin.com",
    "jobs-noreply@linkedin.com",
    "jobs-listings@linkedin.com",
)


@dataclass(frozen=True)
class ExtractedJob:
    """A vacancy extracted from an email."""

    job_id: str
    url: str
    title: str | None


def canonical_job_url(job_id: str) -> str:
    """Canonical and clean URL of a vacancy from its id."""
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


class _AnchorCollector(HTMLParser):
    """Collect (href, text) pairs from each ``<a>`` in the HTML."""

    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.anchors: list[tuple[str, str]] = []
        self._href: str | None = None
        self._text_parts: list[str] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        if tag.lower() != "a":
            return
        href = dict(attrs).get("href")
        self._href = href or ""
        self._text_parts = []

    def handle_data(self, data: str) -> None:
        if self._href is not None:
            self._text_parts.append(data)

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() != "a" or self._href is None:
            return
        text = re.sub(r"\s+", " ", "".join(self._text_parts)).strip()
        self.anchors.append((self._href, text))
        self._href = None
        self._text_parts = []


def extract_jobs_from_html(html: str) -> list[ExtractedJob]:
    """Extract vacancies (id, canonical URL, title) from an email's HTML.

    Strategy: iterate over the ``<a>``; for each href pointing to a vacancy,
    keep the first non-empty title per id. If no anchors matched
    (e.g., links only in tracking params), fall back to a regex on the raw HTML
    to at least capture the ids (without title).
    """
    if not html:
        return []
    collector = _AnchorCollector()
    try:
        collector.feed(html)
    except Exception:
        collector.anchors = []

    titles: dict[str, str] = {}
    order: list[str] = []
    for href, text in collector.anchors:
        # Decode the href: some emails wrap the real URL
        # percent-encoded inside a tracking redirect (/e/v2?url=...).
        match = JOB_ID_RE.search(unquote(href or ""))
        if not match:
            continue
        job_id = match.group(1)
        if job_id not in titles:
            order.append(job_id)
            titles[job_id] = ""
        if text and not titles[job_id]:
            titles[job_id] = text

    if not order:
        # Fallback: ids present in the HTML even if not in an anchor href.
        for job_id in dict.fromkeys(JOB_ID_RE.findall(unquote(html))):
            order.append(job_id)
            titles[job_id] = ""

    return [ExtractedJob(job_id=jid, url=canonical_job_url(jid), title=(titles[jid] or None)) for jid in order]


def build_sender_query(senders: tuple[str, ...] = DEFAULT_ALERT_SENDERS, *, newer_than: str = "7d") -> str:
    """Build the Gmail search query for LinkedIn alerts."""
    from_clause = " OR ".join(senders)
    return f"from:({from_clause}) newer_than:{newer_than}"
