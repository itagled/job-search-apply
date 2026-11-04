"""Idempotent upsert of ``job_post`` by ``(source, external_id)``: ``NormalizedPost`` +
``ingest_job_post`` + ``content_fingerprint``."""

from __future__ import annotations

import hashlib
import re
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import datetime
from itertools import batched
from typing import Any
from urllib.parse import urlparse, urlunparse

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobradar.db.models import JobPost, PersonPost
from jobradar.db.session import insert_for

_URL_RE = re.compile(r"https?://[^\s)>\]]+", re.IGNORECASE)
_WS_RE = re.compile(r"\s+")


@dataclass(frozen=True)
class NormalizedPost:
    """Neutral form of a job offer ready to persist."""

    external_id: str
    post_url: str | None
    title: str | None = None
    company: str | None = None
    location: str | None = None
    posted_at: datetime | None = None
    raw_content: str | None = None
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass
class UpsertStats:
    """Counters for the ingestion batch."""

    inserted: int = 0
    updated: int = 0
    skipped: int = 0

    def as_dict(self) -> dict[str, int]:
        return {"inserted": self.inserted, "updated": self.updated, "skipped": self.skipped}


def _canonicalize_url(url: str) -> str:
    """Strip query/fragment and trailing slash from a URL (recycled from dedupe.py)."""
    parsed = urlparse(url.strip())
    if not parsed.scheme:
        parsed = urlparse(f"https://{url.strip().lstrip('/')}")
    path = parsed.path.rstrip("/") or parsed.path
    return urlunparse((parsed.scheme.lower(), parsed.netloc.lower(), path, "", "", ""))


def content_fingerprint(raw_content: str | None) -> str | None:
    """SHA-256 of the normalized content (to detect equivalence)."""
    if not raw_content or not raw_content.strip():
        return None
    text = raw_content.strip().lower()
    text = _URL_RE.sub(lambda m: _canonicalize_url(m.group(0)), text)
    text = _WS_RE.sub(" ", text)
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def normalize_external_id(
    *,
    activity_id: str | None = None,
    post_url: str | None = None,
) -> str:
    """Canonical external id of a post: prefers ``activityId`` over the URL.

    Guarantees that the same
    post fetched twice lands in the same row via ``(source, external_id)``.
    """
    if activity_id is not None and str(activity_id).strip():
        return str(activity_id).strip()
    if post_url is not None and str(post_url).strip():
        return _canonicalize_url(str(post_url).strip())
    raise ValueError("normalize_external_id: missing activity_id and post_url")


def ingest_job_post(
    session: Session,
    *,
    source: str,
    post: NormalizedPost,
    stats: UpsertStats,
) -> tuple[int, bool]:
    """Insert or refresh a job offer. Idempotent by ``(source, external_id)``.

    Input: session, ``source``, ``NormalizedPost`` and the ``stats`` bag.
    Output: ``(job_post_id, True if insert / False if update)``.

    On update only raw fields are refreshed; ``status`` is not touched to avoid
    "resurrecting" already notified/discarded offers.
    """
    existing = session.scalar(
        select(JobPost).where(
            JobPost.source == source,
            JobPost.external_id == post.external_id,
        )
    )
    fingerprint = content_fingerprint(post.raw_content)
    if existing is None:
        row = JobPost(
            source=source,
            external_id=post.external_id,
            title=post.title,
            company=post.company,
            location=post.location,
            post_url=post.post_url,
            posted_at=post.posted_at,
            raw_content=post.raw_content,
            content_fingerprint=fingerprint,
            status="new",
            source_metadata=post.source_metadata or {},
        )
        session.add(row)
        session.flush()
        stats.inserted += 1
        return int(row.id), True

    # Update: fill fields if new data arrives, without overwriting with None.
    existing.title = post.title or existing.title
    existing.company = post.company or existing.company
    existing.location = post.location or existing.location
    existing.post_url = post.post_url or existing.post_url
    existing.posted_at = post.posted_at or existing.posted_at
    if post.raw_content:
        existing.raw_content = post.raw_content
        existing.content_fingerprint = fingerprint
    if post.source_metadata:
        merged = dict(existing.source_metadata or {})
        merged.update(post.source_metadata)
        existing.source_metadata = merged
    stats.updated += 1
    return int(existing.id), False


def _bulk_upsert_statement(dialect: str, rows: list[dict[str, Any]]) -> Any:
    """``INSERT ... ON CONFLICT DO UPDATE`` of ``job_post`` for Postgres or SQLite."""
    table = JobPost.__table__
    stmt = insert_for(dialect)(table).values(rows)
    new = stmt.excluded
    merged_metadata = (
        table.c.source_metadata.op("||")(new.source_metadata)
        if dialect == "postgresql"
        else func.json_patch(table.c.source_metadata, new.source_metadata)
    )
    keep = ("title", "company", "location", "post_url", "posted_at", "raw_content", "content_fingerprint")
    return stmt.on_conflict_do_update(
        index_elements=["source", "external_id"],
        set_={
            **{col: func.coalesce(new[col], table.c[col]) for col in keep},
            "source_metadata": merged_metadata,
            "updated_at": func.now(),
        },
    )


def bulk_upsert_job_posts(
    session: Session,
    *,
    source: str,
    posts: Iterable[NormalizedPost],
    chunk_size: int = 500,
) -> UpsertStats:
    """Bulk load: one ``INSERT ... ON CONFLICT (source, external_id) DO UPDATE`` per batch.

    Same rule as ``ingest_job_post``: on conflict fill fields without overwriting with NULL,
    refresh the raw if it arrives and merge ``source_metadata``; never touch ``status``. If a
    notice comes repeated within a batch, the last one wins. Works on Postgres and SQLite.
    """
    dialect = session.get_bind().dialect.name
    stats = UpsertStats()
    for batch in batched(posts, chunk_size):
        rows = {
            p.external_id: {
                "source": source,
                "external_id": p.external_id,
                "title": p.title,
                "company": p.company,
                "location": p.location,
                "post_url": p.post_url,
                "posted_at": p.posted_at,
                "raw_content": p.raw_content,
                "content_fingerprint": content_fingerprint(p.raw_content),
                "status": "new",
                "source_metadata": p.source_metadata or {},
            }
            for p in batch
        }
        existing = set(
            session.scalars(select(JobPost.external_id).where(JobPost.source == source, JobPost.external_id.in_(rows)))
        )
        session.execute(_bulk_upsert_statement(dialect, list(rows.values())))
        stats.inserted += len(rows) - len(existing)
        stats.updated += len(existing)
    return stats


# --------------------------------------------------------------------------- #
# person_post: raw posts from monitored profiles (pre-LLM classification).
# --------------------------------------------------------------------------- #


@dataclass(frozen=True)
class NormalizedPersonPost:
    """Neutral form of a person's post, ready to persist."""

    external_id: str
    post_url: str | None = None
    author_linkedin_url: str | None = None
    contact_id: int | None = None
    company_id: int | None = None
    posted_at: datetime | None = None
    raw_content: str | None = None
    raw_payload: dict[str, Any] | None = None


def upsert_person_post(
    session: Session,
    *,
    source: str,
    post: NormalizedPersonPost,
    stats: UpsertStats,
) -> tuple[int, bool]:
    """Insert or refresh a post. Idempotent by ``(source, external_id)``.

    New → insert with ``is_job_post=None, enrichment_status='pending'``.
    Existing with same ``content_fingerprint`` → no-op (no re-classification).
    Existing with different content → refresh raw and re-enqueue enrichment.
    Output: ``(person_post_id, True if insert / False if update)``.
    """
    existing = session.scalar(
        select(PersonPost).where(
            PersonPost.source == source,
            PersonPost.external_id == post.external_id,
        )
    )
    fingerprint = content_fingerprint(post.raw_content)

    if existing is None:
        row = PersonPost(
            source=source,
            external_id=post.external_id,
            contact_id=post.contact_id,
            company_id=post.company_id,
            author_linkedin_url=post.author_linkedin_url,
            post_url=post.post_url,
            posted_at=post.posted_at,
            raw_content=post.raw_content,
            raw_payload=post.raw_payload,
            content_fingerprint=fingerprint,
            enrichment_status="pending",
        )
        session.add(row)
        session.flush()
        stats.inserted += 1
        return int(row.id), True

    # Fill link to contact/company if missing.
    existing.contact_id = existing.contact_id or post.contact_id
    existing.company_id = existing.company_id or post.company_id
    existing.author_linkedin_url = existing.author_linkedin_url or post.author_linkedin_url
    existing.post_url = post.post_url or existing.post_url
    existing.posted_at = post.posted_at or existing.posted_at

    # Text changed (post edit): refresh and re-enqueue classification.
    if fingerprint and fingerprint != existing.content_fingerprint:
        existing.raw_content = post.raw_content
        existing.raw_payload = post.raw_payload
        existing.content_fingerprint = fingerprint
        existing.enrichment_status = "pending"
        existing.is_job_post = None
        existing.detected_role = None
        existing.role_bucket = None
        existing.enrichment_json = None
        existing.enrichment_error = None
        existing.enriched_at = None
    stats.updated += 1
    return int(existing.id), False
