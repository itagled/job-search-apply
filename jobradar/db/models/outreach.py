"""Applications: contacts for each application and the events of its lifecycle."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    CheckConstraint,
    ForeignKey,
    Identity,
    Index,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from jobradar.db.models.base import Base, JSONType, PKType, TZDateTime, one_of

# Event types for an application's lifecycle.
EVENT_TYPES = (
    "applied",  # application sent
    "application_pending",  # application started, something missing from the candidate
    "application_received",  # automatic acknowledgment from the ATS
    "email_sent",  # email from the candidate to the company or decision maker
    "email_reply",  # human response from the company
    "linkedin_message_sent",
    "linkedin_invite_sent",
    "linkedin_reply",
    "assessment_requested",  # test, video, or task requested
    "assessment_done",
    "interview_requested",
    "interview_scheduled",
    "interview_done",
    "rejected",
    "offer",
    "withdrawn",
    "follow_up_sent",
    "other",
)
EVENT_DIRECTIONS = ("outbound", "inbound", "internal")
EVENT_CHANNELS = ("email", "linkedin", "ats", "calendar", "whatsapp", "phone", "other")


class ApplicationContact(Base):
    """Decision maker for an application and the email written to them.

    One contact per (company, email). ``company`` and ``job_post_id`` allow grouping by
    company even if the application has multiple rows in ``job_post`` (multiple sources).
    """

    __tablename__ = "application_contact"

    id: Mapped[int] = mapped_column(PKType, Identity(), primary_key=True)
    job_post_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("job_post.id", ondelete="SET NULL"))
    company: Mapped[str] = mapped_column(Text, nullable=False)
    company_domain: Mapped[str | None] = mapped_column(Text)
    job_title: Mapped[str | None] = mapped_column(Text)
    full_name: Mapped[str | None] = mapped_column(Text)
    first_name: Mapped[str | None] = mapped_column(Text)
    title: Mapped[str | None] = mapped_column(Text)
    role_type: Mapped[str | None] = mapped_column(Text)  # hiring_manager | recruiter | founder | exec
    linkedin_url: Mapped[str | None] = mapped_column(Text)
    email: Mapped[str | None] = mapped_column(Text)
    email_status: Mapped[str | None] = mapped_column(Text)  # verified | guessed | unavailable | ...
    country: Mapped[str | None] = mapped_column(Text)
    language: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str | None] = mapped_column(Text)  # apollo | google | ...
    apollo_id: Mapped[str | None] = mapped_column(Text)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict, server_default=text("'{}'"))
    email_subject: Mapped[str | None] = mapped_column(Text)
    email_body: Mapped[str | None] = mapped_column(Text)
    cv_file: Mapped[str | None] = mapped_column(Text)
    # pending | ready | ready_draft | draft | to_schedule | scheduled_in_gmail | sent | skipped | error
    outreach_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    gmail_message_id: Mapped[str | None] = mapped_column(Text)
    gmail_draft_id: Mapped[str | None] = mapped_column(Text)
    sent_at: Mapped[datetime | None] = mapped_column(TZDateTime())
    notes: Mapped[str | None] = mapped_column(Text)
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False, server_default=func.now())
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    __table_args__ = (
        Index("idx_application_contact_job_post", "job_post_id"),
        Index("idx_application_contact_status", "outreach_status"),
        Index(
            "uq_application_contact_company_email",
            func.lower(company),
            func.lower(email),
            unique=True,
            postgresql_where=text("email IS NOT NULL"),
            sqlite_where=text("email IS NOT NULL"),
        ),
    )


class ApplicationEvent(Base):
    """An event in an application's lifecycle (applied, response, interview, rejection...).

    ``job_post_id`` points to the canonical row of the posting (the one with the lowest id among those sharing
    ``external_id``); ``job_external_id`` groups even if there are duplicates. Unique ``dedupe_key``
    prevents loading the same email or event twice.
    """

    __tablename__ = "application_event"

    id: Mapped[int] = mapped_column(PKType, Identity(), primary_key=True)
    job_post_id: Mapped[int | None] = mapped_column(PKType, ForeignKey("job_post.id", ondelete="SET NULL"))
    job_external_id: Mapped[str | None] = mapped_column(Text)
    company: Mapped[str | None] = mapped_column(Text)
    event_type: Mapped[str] = mapped_column(Text, nullable=False)
    direction: Mapped[str | None] = mapped_column(Text)
    channel: Mapped[str | None] = mapped_column(Text)
    occurred_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False)
    scheduled_for: Mapped[datetime | None] = mapped_column(TZDateTime())  # interview or deadline
    contact_name: Mapped[str | None] = mapped_column(Text)
    contact_email: Mapped[str | None] = mapped_column(Text)
    application_contact_id: Mapped[int | None] = mapped_column(
        PKType, ForeignKey("application_contact.id", ondelete="SET NULL")
    )
    subject: Mapped[str | None] = mapped_column(Text)
    summary: Mapped[str | None] = mapped_column(Text)
    gmail_message_id: Mapped[str | None] = mapped_column(Text)
    gmail_thread_id: Mapped[str | None] = mapped_column(Text)
    calendar_event_id: Mapped[str | None] = mapped_column(Text)
    url: Mapped[str | None] = mapped_column(Text)
    source: Mapped[str] = mapped_column(Text, nullable=False)  # db_backfill | gmail | calendar | linkedin | manual
    confidence: Mapped[str | None] = mapped_column(Text)  # high | medium | low
    dedupe_key: Mapped[str] = mapped_column(Text, nullable=False)
    raw: Mapped[dict[str, Any]] = mapped_column(JSONType, nullable=False, default=dict, server_default=text("'{}'"))
    created_at: Mapped[datetime] = mapped_column(TZDateTime(), nullable=False, server_default=func.now())

    __table_args__ = (
        UniqueConstraint("dedupe_key", name="application_event_dedupe_key_key"),
        Index("idx_application_event_job", "job_post_id", "occurred_at"),
        Index("idx_application_event_ext", "job_external_id"),
        Index("idx_application_event_type", "event_type"),
        CheckConstraint(one_of("event_type", EVENT_TYPES), name="application_event_event_type_check"),
        CheckConstraint(one_of("direction", EVENT_DIRECTIONS), name="application_event_direction_check"),
        CheckConstraint(one_of("channel", EVENT_CHANNELS), name="application_event_channel_check"),
    )
