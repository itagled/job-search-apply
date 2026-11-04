"""Funnel of target companies, their contacts, and the LinkedIn posts that are monitored."""

from __future__ import annotations

from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    CheckConstraint,
    ForeignKey,
    Index,
    Integer,
    Text,
    UniqueConstraint,
    func,
    text,
)
from sqlalchemy.orm import Mapped, mapped_column

from jobradar.db.models.base import Base, JSONType, PKType, TZDateTime, one_of

# --------------------------------------------------------------------------- #
# Funnel of target companies.
# Each company advances through stages (``stage``); it only passes the gate if it survives the
# heuristic/LLM filter and human review (HITL via xlsx export/import).
# --------------------------------------------------------------------------- #

# Funnel stages (see jobradar/companies/run.py).
COMPANY_STAGES = (
    "raw",  # freshly extracted (S0)
    "screened",  # cheap heuristic applied (S1)
    "base_enriched",  # enriched with Apollo/Serper base (S2)
    "rescreened",  # complex heuristic applied (S3)
    "classified",  # classified with LLM (S4)
    "contacts_done",  # contacts extracted (S5)
    "dropped",  # discarded by review/gate
)

# Human review decisions (editable column in the xlsx).
REVIEW_DECISIONS = ("keep", "promote", "hold", "drop")


class TargetCompany(Base):
    __tablename__ = "target_company"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)

    # Identity.
    name: Mapped[str] = mapped_column(Text, nullable=False)
    name_normalized: Mapped[str | None] = mapped_column(Text, nullable=True)
    domain: Mapped[str | None] = mapped_column(Text, nullable=True)
    website: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Firmographics.
    industry: Mapped[str | None] = mapped_column(Text, nullable=True)
    # Raw Apollo industry grouped into ~40 categories (see jobradar/companies/industry_groups.py).
    industry_group: Mapped[str | None] = mapped_column(Text, nullable=True)
    employee_count: Mapped[int | None] = mapped_column(Integer, nullable=True)
    employee_count_range: Mapped[str | None] = mapped_column(Text, nullable=True)
    description: Mapped[str | None] = mapped_column(Text, nullable=True)
    country: Mapped[str | None] = mapped_column(Text, nullable=True)
    city: Mapped[str | None] = mapped_column(Text, nullable=True)
    founded_year: Mapped[int | None] = mapped_column(Integer, nullable=True)

    # Provenance (multiple possible sources per company).
    sources: Mapped[list[Any]] = mapped_column(JSONType, nullable=False, default=list, server_default=text("'[]'"))
    first_source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default=text("'{}'")
    )

    # Funnel status.
    stage: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'raw'"))
    stage_updated_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    # Heuristic scoring (S1/S3).
    heuristic_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    heuristic_signals: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default=text("'{}'")
    )
    tier_pre: Mapped[str | None] = mapped_column(Text, nullable=True)

    # LLM classification (S4).
    is_startup: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    company_type: Mapped[str | None] = mapped_column(Text, nullable=True)
    business_model: Mapped[str | None] = mapped_column(Text, nullable=True)
    fit_tier: Mapped[str | None] = mapped_column(Text, nullable=True)
    fit_score: Mapped[int | None] = mapped_column(Integer, nullable=True)
    fit_reason: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Human review (HITL).
    review_decision: Mapped[str | None] = mapped_column(Text, nullable=True)
    review_notes: Mapped[str | None] = mapped_column(Text, nullable=True)
    reviewed_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    created_at: Mapped[datetime] = mapped_column(TZDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # Dedup by domain (case-insensitive) when it exists.
        Index(
            "uq_target_company_domain_lower",
            func.lower(domain),
            unique=True,
            postgresql_where=text("domain IS NOT NULL"),
            sqlite_where=text("domain IS NOT NULL"),
        ),
        Index("idx_target_company_name_norm", "name_normalized"),
        Index("idx_target_company_stage", "stage"),
        Index("idx_target_company_fit_tier", "fit_tier"),
        CheckConstraint(one_of("stage", COMPANY_STAGES), name="ck_target_company_stage"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"<TargetCompany {self.name!r} [{self.stage}] tier={self.fit_tier}>"


class CompanyContact(Base):
    __tablename__ = "company_contact"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)
    company_id: Mapped[int] = mapped_column(PKType, ForeignKey("target_company.id", ondelete="CASCADE"), nullable=False)

    full_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    first_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    last_name: Mapped[str | None] = mapped_column(Text, nullable=True)
    jobtitle: Mapped[str | None] = mapped_column(Text, nullable=True)
    seniority: Mapped[str | None] = mapped_column(Text, nullable=True)
    # founder | ceo | c_level | sales | growth | other
    role_category: Mapped[str | None] = mapped_column(Text, nullable=True)
    linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    email: Mapped[str | None] = mapped_column(Text, nullable=True)
    email_status: Mapped[str | None] = mapped_column(Text, nullable=True)
    phone: Mapped[str | None] = mapped_column(Text, nullable=True)

    source: Mapped[str | None] = mapped_column(Text, nullable=True)
    source_metadata: Mapped[dict[str, Any]] = mapped_column(
        JSONType, nullable=False, default=dict, server_default=text("'{}'")
    )

    created_at: Mapped[datetime] = mapped_column(TZDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        # Dedup by (company, LinkedIn profile) when there is a URL.
        Index(
            "uq_company_contact_company_linkedin_lower",
            "company_id",
            func.lower(linkedin_url),
            unique=True,
            postgresql_where=text("linkedin_url IS NOT NULL"),
            sqlite_where=text("linkedin_url IS NOT NULL"),
        ),
        Index("idx_company_contact_company", "company_id"),
        Index("idx_company_contact_role", "role_category"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"<CompanyContact {self.full_name!r} [{self.role_category}] co={self.company_id}>"


# --------------------------------------------------------------------------- #
# LinkedIn posts of monitored people (talent/founders/sales) from
# prioritized startups. They are scraped with Apify, classified with LLM, and those that
# turn out to be target openings are promoted to ``job_post`` (fit scoring).
# --------------------------------------------------------------------------- #


class PersonPost(Base):
    __tablename__ = "person_post"

    id: Mapped[int] = mapped_column(PKType, primary_key=True, autoincrement=True)

    # Source and external id. For the profile monitor: source
    # ``linkedin_profile_monitor`` and external_id = activityId of the post.
    source: Mapped[str] = mapped_column(Text, nullable=False)
    external_id: Mapped[str] = mapped_column(Text, nullable=False)

    # Who/which company the post belongs to (both optional to not lose data).
    contact_id: Mapped[int | None] = mapped_column(
        PKType, ForeignKey("company_contact.id", ondelete="SET NULL"), nullable=True
    )
    company_id: Mapped[int | None] = mapped_column(
        PKType, ForeignKey("target_company.id", ondelete="SET NULL"), nullable=True
    )
    author_linkedin_url: Mapped[str | None] = mapped_column(Text, nullable=True)

    # Raw post content.
    post_url: Mapped[str | None] = mapped_column(Text, nullable=True)
    posted_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)
    raw_content: Mapped[str | None] = mapped_column(Text, nullable=True)
    raw_payload: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    content_fingerprint: Mapped[str | None] = mapped_column(Text, nullable=True)

    # LLM classification (is it an offer? what role?).
    is_job_post: Mapped[bool | None] = mapped_column(Boolean, nullable=True)
    detected_role: Mapped[str | None] = mapped_column(Text, nullable=True)
    role_bucket: Mapped[str | None] = mapped_column(Text, nullable=True)
    hiring_company: Mapped[str | None] = mapped_column(Text, nullable=True)
    enrichment_json: Mapped[dict[str, Any] | None] = mapped_column(JSONType, nullable=True)
    enrichment_status: Mapped[str] = mapped_column(Text, nullable=False, server_default=text("'pending'"))
    enrichment_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    enriched_at: Mapped[datetime | None] = mapped_column(TZDateTime(), nullable=True)

    # Promotion to ``job_post`` when the offer falls into the target set.
    job_post_id: Mapped[int | None] = mapped_column(
        PKType, ForeignKey("job_post.id", ondelete="SET NULL"), nullable=True
    )

    created_at: Mapped[datetime] = mapped_column(TZDateTime(), server_default=func.now(), nullable=False)
    updated_at: Mapped[datetime] = mapped_column(
        TZDateTime(), server_default=func.now(), onupdate=func.now(), nullable=False
    )

    __table_args__ = (
        UniqueConstraint("source", "external_id", name="uq_person_post_source_external_id"),
        Index("idx_person_post_enrichment_status", "enrichment_status"),
        Index("idx_person_post_is_job_post", "is_job_post"),
        Index("idx_person_post_contact", "contact_id"),
        Index("idx_person_post_company", "company_id"),
    )

    def __repr__(self) -> str:  # pragma: no cover - debug convenience
        return f"<PersonPost {self.source}:{self.external_id} job={self.is_job_post} bucket={self.role_bucket}>"
