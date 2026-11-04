"""Initial schema: postings, target companies, contacts and the lifecycle of each application.

Revision: 0001
Previous:
"""

from __future__ import annotations

import sqlalchemy as sa
from alembic import op
from sqlalchemy.dialects import postgresql

revision = "0001"
down_revision = None
branch_labels = None
depends_on = None

ID = sa.BigInteger().with_variant(sa.Integer(), "sqlite")
JSON = sa.JSON().with_variant(postgresql.JSONB(), "postgresql")
TIMESTAMP = sa.DateTime(timezone=True)
NOW = sa.func.now()
TABLES = (
    "job_post",
    "target_company",
    "application_contact",
    "company_contact",
    "application_event",
    "person_post",
)


def _lower(*columns: str) -> list[sa.ColumnElement]:
    return [sa.literal_column(f"lower({column})") for column in columns]


def _only_with(column: str) -> dict[str, sa.TextClause]:
    """Partial index: only the rows that have a value in ``column``."""
    return {"postgresql_where": sa.text(f"{column} IS NOT NULL"), "sqlite_where": sa.text(f"{column} IS NOT NULL")}


def upgrade() -> None:
    op.create_table(
        "job_post",
        sa.Column("id", ID, autoincrement=True, nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("location", sa.Text(), nullable=True),
        sa.Column("post_url", sa.Text(), nullable=True),
        sa.Column("posted_at", TIMESTAMP, nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("content_fingerprint", sa.Text(), nullable=True),
        sa.Column("job_description_full", sa.Text(), nullable=True),
        sa.Column("description_fetch_status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("description_fetched_at", TIMESTAMP, nullable=True),
        sa.Column("fit_score", sa.Integer(), nullable=True),
        sa.Column("fit_tier", sa.Text(), nullable=True),
        sa.Column("fit_json", JSON, nullable=True),
        sa.Column("fit_status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("fit_scored_at", TIMESTAMP, nullable=True),
        sa.Column("fit_error", sa.Text(), nullable=True),
        sa.Column("status", sa.Text(), server_default=sa.text("'new'"), nullable=False),
        sa.Column("source_metadata", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.CheckConstraint("status IN ('new', 'notified', 'applied', 'discarded')", name="ck_job_post_status"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_job_post_source_external_id"),
    )
    op.create_index("idx_job_post_created_at", "job_post", [sa.literal_column("created_at DESC")], unique=False)
    op.create_index("idx_job_post_description_fetch_status", "job_post", ["description_fetch_status"], unique=False)
    op.create_index("idx_job_post_fit_status", "job_post", ["fit_status"], unique=False)
    op.create_index("idx_job_post_status", "job_post", ["status"], unique=False)

    op.create_table(
        "target_company",
        sa.Column("id", ID, autoincrement=True, nullable=False),
        sa.Column("name", sa.Text(), nullable=False),
        sa.Column("name_normalized", sa.Text(), nullable=True),
        sa.Column("domain", sa.Text(), nullable=True),
        sa.Column("website", sa.Text(), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("industry", sa.Text(), nullable=True),
        sa.Column("industry_group", sa.Text(), nullable=True),
        sa.Column("employee_count", sa.Integer(), nullable=True),
        sa.Column("employee_count_range", sa.Text(), nullable=True),
        sa.Column("description", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("city", sa.Text(), nullable=True),
        sa.Column("founded_year", sa.Integer(), nullable=True),
        sa.Column("sources", JSON, server_default=sa.text("'[]'"), nullable=False),
        sa.Column("first_source", sa.Text(), nullable=True),
        sa.Column("source_metadata", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("stage", sa.Text(), server_default=sa.text("'raw'"), nullable=False),
        sa.Column("stage_updated_at", TIMESTAMP, nullable=True),
        sa.Column("heuristic_score", sa.Integer(), nullable=True),
        sa.Column("heuristic_signals", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("tier_pre", sa.Text(), nullable=True),
        sa.Column("is_startup", sa.Boolean(), nullable=True),
        sa.Column("company_type", sa.Text(), nullable=True),
        sa.Column("business_model", sa.Text(), nullable=True),
        sa.Column("fit_tier", sa.Text(), nullable=True),
        sa.Column("fit_score", sa.Integer(), nullable=True),
        sa.Column("fit_reason", sa.Text(), nullable=True),
        sa.Column("review_decision", sa.Text(), nullable=True),
        sa.Column("review_notes", sa.Text(), nullable=True),
        sa.Column("reviewed_at", TIMESTAMP, nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.CheckConstraint(
            "stage IN ('raw', 'screened', 'base_enriched', 'rescreened', 'classified', 'contacts_done', 'dropped')",
            name="ck_target_company_stage",
        ),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_target_company_fit_tier", "target_company", ["fit_tier"], unique=False)
    op.create_index("idx_target_company_name_norm", "target_company", ["name_normalized"], unique=False)
    op.create_index("idx_target_company_stage", "target_company", ["stage"], unique=False)

    op.create_table(
        "application_contact",
        sa.Column("id", ID, sa.Identity(always=False), nullable=False),
        sa.Column("job_post_id", ID, nullable=True),
        sa.Column("company", sa.Text(), nullable=False),
        sa.Column("company_domain", sa.Text(), nullable=True),
        sa.Column("job_title", sa.Text(), nullable=True),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("title", sa.Text(), nullable=True),
        sa.Column("role_type", sa.Text(), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("email_status", sa.Text(), nullable=True),
        sa.Column("country", sa.Text(), nullable=True),
        sa.Column("language", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("apollo_id", sa.Text(), nullable=True),
        sa.Column("raw", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("email_subject", sa.Text(), nullable=True),
        sa.Column("email_body", sa.Text(), nullable=True),
        sa.Column("cv_file", sa.Text(), nullable=True),
        sa.Column("outreach_status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("gmail_message_id", sa.Text(), nullable=True),
        sa.Column("gmail_draft_id", sa.Text(), nullable=True),
        sa.Column("sent_at", TIMESTAMP, nullable=True),
        sa.Column("notes", sa.Text(), nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["job_post_id"], ["job_post.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_application_contact_job_post", "application_contact", ["job_post_id"], unique=False)
    op.create_index("idx_application_contact_status", "application_contact", ["outreach_status"], unique=False)

    op.create_table(
        "company_contact",
        sa.Column("id", ID, autoincrement=True, nullable=False),
        sa.Column("company_id", ID, nullable=False),
        sa.Column("full_name", sa.Text(), nullable=True),
        sa.Column("first_name", sa.Text(), nullable=True),
        sa.Column("last_name", sa.Text(), nullable=True),
        sa.Column("jobtitle", sa.Text(), nullable=True),
        sa.Column("seniority", sa.Text(), nullable=True),
        sa.Column("role_category", sa.Text(), nullable=True),
        sa.Column("linkedin_url", sa.Text(), nullable=True),
        sa.Column("email", sa.Text(), nullable=True),
        sa.Column("email_status", sa.Text(), nullable=True),
        sa.Column("phone", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=True),
        sa.Column("source_metadata", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["target_company.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index("idx_company_contact_company", "company_contact", ["company_id"], unique=False)
    op.create_index("idx_company_contact_role", "company_contact", ["role_category"], unique=False)

    op.create_table(
        "application_event",
        sa.Column("id", ID, sa.Identity(always=False), nullable=False),
        sa.Column("job_post_id", ID, nullable=True),
        sa.Column("job_external_id", sa.Text(), nullable=True),
        sa.Column("company", sa.Text(), nullable=True),
        sa.Column("event_type", sa.Text(), nullable=False),
        sa.Column("direction", sa.Text(), nullable=True),
        sa.Column("channel", sa.Text(), nullable=True),
        sa.Column("occurred_at", TIMESTAMP, nullable=False),
        sa.Column("scheduled_for", TIMESTAMP, nullable=True),
        sa.Column("contact_name", sa.Text(), nullable=True),
        sa.Column("contact_email", sa.Text(), nullable=True),
        sa.Column("application_contact_id", ID, nullable=True),
        sa.Column("subject", sa.Text(), nullable=True),
        sa.Column("summary", sa.Text(), nullable=True),
        sa.Column("gmail_message_id", sa.Text(), nullable=True),
        sa.Column("gmail_thread_id", sa.Text(), nullable=True),
        sa.Column("calendar_event_id", sa.Text(), nullable=True),
        sa.Column("url", sa.Text(), nullable=True),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("confidence", sa.Text(), nullable=True),
        sa.Column("dedupe_key", sa.Text(), nullable=False),
        sa.Column("raw", JSON, server_default=sa.text("'{}'"), nullable=False),
        sa.Column("created_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.CheckConstraint(
            "channel IN ('email', 'linkedin', 'ats', 'calendar', 'whatsapp', 'phone', 'other')",
            name="application_event_channel_check",
        ),
        sa.CheckConstraint(
            "direction IN ('outbound', 'inbound', 'internal')", name="application_event_direction_check"
        ),
        sa.CheckConstraint(
            "event_type IN ('applied', 'application_pending', 'application_received', 'email_sent', 'email_reply', 'linkedin_message_sent', 'linkedin_invite_sent', 'linkedin_reply', 'assessment_requested', 'assessment_done', 'interview_requested', 'interview_scheduled', 'interview_done', 'rejected', 'offer', 'withdrawn', 'follow_up_sent', 'other')",
            name="application_event_event_type_check",
        ),
        sa.ForeignKeyConstraint(["application_contact_id"], ["application_contact.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_post_id"], ["job_post.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("dedupe_key", name="application_event_dedupe_key_key"),
    )
    op.create_index("idx_application_event_ext", "application_event", ["job_external_id"], unique=False)
    op.create_index("idx_application_event_job", "application_event", ["job_post_id", "occurred_at"], unique=False)
    op.create_index("idx_application_event_type", "application_event", ["event_type"], unique=False)

    op.create_table(
        "person_post",
        sa.Column("id", ID, autoincrement=True, nullable=False),
        sa.Column("source", sa.Text(), nullable=False),
        sa.Column("external_id", sa.Text(), nullable=False),
        sa.Column("contact_id", ID, nullable=True),
        sa.Column("company_id", ID, nullable=True),
        sa.Column("author_linkedin_url", sa.Text(), nullable=True),
        sa.Column("post_url", sa.Text(), nullable=True),
        sa.Column("posted_at", TIMESTAMP, nullable=True),
        sa.Column("raw_content", sa.Text(), nullable=True),
        sa.Column("raw_payload", JSON, nullable=True),
        sa.Column("content_fingerprint", sa.Text(), nullable=True),
        sa.Column("is_job_post", sa.Boolean(), nullable=True),
        sa.Column("detected_role", sa.Text(), nullable=True),
        sa.Column("role_bucket", sa.Text(), nullable=True),
        sa.Column("hiring_company", sa.Text(), nullable=True),
        sa.Column("enrichment_json", JSON, nullable=True),
        sa.Column("enrichment_status", sa.Text(), server_default=sa.text("'pending'"), nullable=False),
        sa.Column("enrichment_error", sa.Text(), nullable=True),
        sa.Column("enriched_at", TIMESTAMP, nullable=True),
        sa.Column("job_post_id", ID, nullable=True),
        sa.Column("created_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.Column("updated_at", TIMESTAMP, server_default=NOW, nullable=False),
        sa.ForeignKeyConstraint(["company_id"], ["target_company.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["contact_id"], ["company_contact.id"], ondelete="SET NULL"),
        sa.ForeignKeyConstraint(["job_post_id"], ["job_post.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("source", "external_id", name="uq_person_post_source_external_id"),
    )
    op.create_index("idx_person_post_company", "person_post", ["company_id"], unique=False)
    op.create_index("idx_person_post_contact", "person_post", ["contact_id"], unique=False)
    op.create_index("idx_person_post_enrichment_status", "person_post", ["enrichment_status"], unique=False)
    op.create_index("idx_person_post_is_job_post", "person_post", ["is_job_post"], unique=False)

    # On expressions: case-insensitive unique keys (autogenerate skips these on SQLite).
    op.create_index(
        "uq_target_company_domain_lower", "target_company", _lower("domain"), unique=True, **_only_with("domain")
    )
    op.create_index(
        "uq_application_contact_company_email",
        "application_contact",
        _lower("company", "email"),
        unique=True,
        **_only_with("email"),
    )
    op.create_index(
        "uq_company_contact_company_linkedin_lower",
        "company_contact",
        ["company_id", *_lower("linkedin_url")],
        unique=True,
        **_only_with("linkedin_url"),
    )


def downgrade() -> None:
    for table in reversed(TABLES):
        op.drop_table(table)
