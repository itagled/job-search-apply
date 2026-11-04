"""Deduplication across sources: the same job posting may arrive via LinkedIn, its ATS, and a portal.

A posting is skipped if the DB already has one from ANOTHER source with the same canonical URL, or with the same
job title and an equivalent company. If the same source brings it again it is not a duplicate: it is updated.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from typing import Any
from urllib.parse import parse_qsl, urlsplit

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobradar.db.models import JobPost
from jobradar.sources.base import Posting

# Query parameters that identify the posting (Indeed ``?jk=``, Greenhouse ``?gh_jid=``...):
# are kept in the canonical URL; the rest (tracking) is discarded.
ID_PARAMS = {
    "jk",
    "vjk",
    "gh_jid",
    "jobid",
    "job_id",
    "jid",
    "id",
    "currentjobid",
    "posting_id",
    "postingid",
    "req",
    "reqid",
    "requisitionid",
    "lever-via",
    "ashby_jid",
    "jobpostingid",
    "job",
}


def canon_url(url: str | None) -> str:
    """Comparable URL: without scheme, ``www.``, fragment, or tracking, but with the id parameters."""
    if not url:
        return ""
    parts = urlsplit(url.split("#")[0].strip())
    base = re.sub(r"^(www\.)?", "", (parts.netloc or "").lower()) + (parts.path or "").rstrip("/").lower()
    keep = sorted((k.lower(), v) for k, v in parse_qsl(parts.query) if k.lower() in ID_PARAMS and v)
    return base + ("?" + "&".join(f"{k}={v}" for k, v in keep) if keep else "")


def key(text: str | None) -> str:
    """Lowercase, without punctuation, and with collapsed spaces (to compare job titles and companies)."""
    return re.sub(r"\s+", " ", re.sub(r"[^\w]+", " ", (text or "").lower())).strip()


def same_company(a: str, b: str) -> bool:
    """``"acme"`` and ``"acme inc"`` are the same company; prefixes shorter than 4 letters do not count."""
    if not a or not b:
        return False
    if a == b:
        return True
    short, long_ = sorted((a, b), key=len)
    return len(short) >= 4 and long_.startswith(short + " ")


class Deduplicator:
    """Index of what is already in ``job_post`` (canonical URL and title+company)."""

    def __init__(self, rows: Iterable[Any] = ()) -> None:
        self.same: set[tuple[str, str]] = set()
        self.by_url: dict[str, tuple[Any, str]] = {}
        self.by_title: dict[str, list[tuple[str, Any, str]]] = {}
        for row in rows:
            self._index(row.id, row.source, str(row.external_id), row.post_url, row.company, row.title)

    @classmethod
    def from_db(cls, session: Session) -> Deduplicator:
        rows = session.execute(
            select(JobPost.id, JobPost.source, JobPost.external_id, JobPost.post_url, JobPost.company, JobPost.title)
        ).all()
        return cls(rows)

    def _index(
        self, job_id: Any, source: str, external_id: str, url: str | None, company: str | None, title: str | None
    ) -> None:
        self.same.add((source, external_id))
        if url:
            self.by_url.setdefault(canon_url(url), (job_id, source))
        self.by_title.setdefault(key(title), []).append((key(company), job_id, source))

    def duplicate(self, source: str, posting: Posting) -> tuple[Any, str] | None:
        """(id, source) of the equivalent posting that already exists from another source, or ``None``."""
        if (source, posting.id) in self.same:
            return None
        for url in (posting.url, posting.apply_url):
            hit = self.by_url.get(canon_url(url)) if url else None
            if hit:
                return hit
        if posting.company:
            company = key(posting.company)
            for other, job_id, other_source in self.by_title.get(key(posting.title), []):
                if same_company(company, other):
                    return job_id, other_source
        return None

    def add(self, source: str, posting: Posting) -> None:
        """Add an accepted posting, so that subsequent sources see it as existing."""
        self._index(None, source, posting.id, posting.url or posting.apply_url, posting.company, posting.title)
