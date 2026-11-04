"""Single job posting loader: filters, deduplicates against the DB and saves in bulk.

For the postings of a connector:

1. title, age and eligibility filter (``filters.accept``);
2. those that already exist from another source are skipped (``dedupe``), but if they bring a description and the existing row
   does not have one, it is completed; those from the same source are updated;
3. ``INSERT ... ON CONFLICT`` per batch, and the full description with ``JobPost.set_description``.

The full posting remains in ``source_metadata[<source>]``, without discarding fields.
"""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterable
from dataclasses import dataclass, field
from datetime import UTC, datetime
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobradar.db.models import JobPost
from jobradar.db.upsert import NormalizedPost, bulk_upsert_job_posts
from jobradar.sources import filters, titles
from jobradar.sources.base import Posting
from jobradar.sources.dedupe import Deduplicator


@dataclass
class Summary:
    source: str
    seen: int = 0
    accepted: int = 0
    duplicates_other_source: int = 0
    new: int = 0
    updated: int = 0
    with_description: int = 0
    discarded: Counter = field(default_factory=Counter)
    dry_run: bool = False

    def as_dict(self) -> dict[str, Any]:
        return {
            "source": self.source,
            "seen": self.seen,
            "accepted": self.accepted,
            "duplicates_other_source": self.duplicates_other_source,
            "new": self.new,
            "updated": self.updated,
            "with_description": self.with_description,
            "discarded": dict(self.discarded.most_common(12)),
            "dry_run": self.dry_run,
        }


def filter_postings(
    postings: Iterable[Posting], *, now: datetime | None = None, apply_filters: bool = True
) -> tuple[list[Posting], Counter, int]:
    """Postings that pass the filters (without duplicates), discard reasons and total seen."""
    postings = list(postings)
    if apply_filters:
        titles.judge(posting.title for posting in postings)
    accepted, discarded, seen, ids = [], Counter(), 0, set()
    for posting in postings:
        seen += 1
        if not posting.id or not posting.title:
            discarded["missing_id_or_title"] += 1
            continue
        if posting.id in ids:
            discarded["duplicate"] += 1
            continue
        ids.add(posting.id)
        ok, reason = filters.accept(posting, now=now) if apply_filters else (True, "")
        if ok:
            accepted.append(posting)
        else:
            discarded[":".join(reason.split(":")[:2])] += 1
    return accepted, discarded, seen


def _post(source: str, posting: Posting, now: datetime) -> NormalizedPost:
    return NormalizedPost(
        external_id=posting.id,
        post_url=posting.url or posting.apply_url,
        title=posting.title,
        company=posting.company,
        location=posting.location,
        posted_at=posting.posted_at,
        raw_content=posting.description or posting.title,
        source_metadata={source: {**posting.metadata(), "ingested_at": now.isoformat()}},
    )


def load(
    source: str,
    postings: Iterable[Posting],
    *,
    session: Session,
    dry_run: bool = False,
    dedupe: Deduplicator | None = None,
    now: datetime | None = None,
    apply_filters: bool = True,
) -> Summary:
    """Filter, deduplicate and save the postings of ``source``. With ``dry_run`` it writes nothing."""
    now = now or datetime.now(UTC)
    accepted, discarded, seen = filter_postings(postings, now=now, apply_filters=apply_filters)
    summary = Summary(source, seen=seen, accepted=len(accepted), discarded=discarded, dry_run=dry_run)
    dedupe = dedupe or Deduplicator.from_db(session)
    to_save, fill_in_others = [], {}
    for posting in accepted:
        existing = dedupe.duplicate(source, posting)
        if existing:
            summary.duplicates_other_source += 1
            if posting.description and existing[0] is not None:
                fill_in_others[existing[0]] = posting.description
            continue
        to_save.append(posting)
        dedupe.add(source, posting)
    if dry_run:
        summary.new = len(to_save)
        return summary

    for job_id, description in fill_in_others.items():  # same offer from another source, without description
        row = session.get(JobPost, job_id)
        if row is not None:
            summary.with_description += row.set_description(description, when=now)
    if not to_save:
        session.commit()
        return summary
    stats = bulk_upsert_job_posts(session, source=source, posts=[_post(source, a, now) for a in to_save])
    summary.new, summary.updated = stats.inserted, stats.updated
    descriptions = {a.id: a.description for a in to_save if a.description}
    ids = list(descriptions)
    for i in range(0, len(ids), 500):
        rows = session.scalars(
            select(JobPost).where(JobPost.source == source, JobPost.external_id.in_(ids[i : i + 500]))
        )
        summary.with_description += sum(f.set_description(descriptions[f.external_id], when=now) for f in rows)
    session.commit()
    return summary
