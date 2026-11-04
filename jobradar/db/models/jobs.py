"""Job postings: the ``job_post`` table.

The natural key ``(source, external_id)`` guarantees idempotency in the upsert. Each posting
stores its description, the fit with the LLM, and its status in the personal cycle.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ColumnElement,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from jobradar.db.models.base import Base, JSONType, PKType, TZDateTime, one_of

# States of the personal cycle of a job offer.
JOB_STATUSES = ("new", "notified", "applied", "discarded")


class JobPost(Base):
    __tablename__ = "job_post"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    # Source and external id (for LinkedIn: numeric id of the vacancy).
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Content of the offer.
    title: Mapped[str | None] = mapped_column(Text, nullable=True)
    company: Mapped[str | None] = mapped_column(Text, nullable=True)
    location: Mapped[str | None] = mapped_column(Text, nullable=True)
    post_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Full description of the vacancy (fetched from an external DB or via Apify).
    job_description_full: Mapped[str | None] = mapped_column(Text, nullable=True)
    description_fetch_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )  # pending | done | error | skipped
    description_fetched_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    # Fit score of the LLM against the CV and the target profile.
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)  # 0-100
    fit_tier: Mapped[str | None] = mapped_column(Text, nullable=True)  # high|medium|low
    fit_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    fit_status: Mapped[str] = mapped_column(
        Text, nullable=False, server_default=text("'pending'")
    )  # pending | done | error | skipped
    fit_scored_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    fit_error: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Personal lifecycle: new -> notified -> applied / discarded.
    status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'new'"))

    # Origin traceability (gmail_message_id, email_subject, href, etc.).
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default=text("'{}'")
    )

    created_at: Mapped[datetime] = mapped_column(TZDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_job_post_source_external_id"),
        Index("idx_job_post_status", "status"),
        Index("idx_job_post_created_at", created_at.desc()),
        Index("idx_job_post_description_fetch_status", "description_fetch_status"),
        Index("idx_job_post_fit_status", "fit_status"),
        CheckConstraint(one_of("status", JOB_STATUSES), name="ck_job_post_status"),
    )

    @classmethod
    def has_metadata(cls, key: str) -> ColumnElement[bool]:
        """SQL condition: ``source_metadata`` has ``key`` with a value (Postgres and SQLite)."""
        return cls.source_metadata[key].as_string().is_not(None)

    def set_description(
        self, description: str | None, *, when: datetime | None = None, overwrite: bool = False
    ) -> bool:
        """Save the full description and mark it ready (``description_fetch_status='done'``).

        Does not overwrite an existing description, unless ``overwrite`` is set. True if saved.
        """
        if not description or not description.strip():
            return False
        if (self.job_description_full or "").strip() and not overwrite:
            return False
        self.job_description_full = description
        self.description_fetch_status = "done"
        self.description_fetched_at = when or datetime.now(UTC)
        return True

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"<JobPost {self.source}:{self.external_id} {self.title!r} [{self.status}]>"
