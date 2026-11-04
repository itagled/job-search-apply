"""Export/import xlsx for human review gates (HITL).

``export_companies`` dumps companies from a stage to an xlsx with an editable
``review_decision`` column (keep/promote/hold/drop). ``import_review`` re-reads that
xlsx and persists the decisions. It also exports contacts for the final review.
"""

from __future__ import annotations

from datetime import UTC, datetime
from pathlib import Path

from openpyxl import Workbook, load_workbook
from sqlalchemy import select
from sqlalchemy.orm import Session

from jobradar.db.models import REVIEW_DECISIONS, CompanyContact, TargetCompany

# Exported columns (fixed order). ``review_decision``/``review_notes`` are editable.
COMPANY_COLUMNS = [
    "id",
    "name",
    "domain",
    "website",
    "linkedin_url",
    "industry",
    "employee_count",
    "employee_count_range",
    "country",
    "city",
    "stage",
    "heuristic_score",
    "tier_pre",
    "business_model",
    "fit_tier",
    "fit_score",
    "fit_reason",
    "description",
    "sources",
    "review_decision",
    "review_notes",
]

CONTACT_COLUMNS = [
    "id",
    "company_id",
    "company_name",
    "full_name",
    "jobtitle",
    "role_category",
    "seniority",
    "linkedin_url",
    "email",
    "email_status",
    "phone",
    "source",
]


def export_companies(session: Session, *, path: str, stage: str | None = None, limit: int | None = None) -> int:
    """Export companies (optionally filtered by stage) to an xlsx.

    Output: number of rows written.
    """
    stmt = select(TargetCompany)
    if stage:
        stmt = stmt.where(TargetCompany.stage == stage)
    stmt = stmt.order_by(
        TargetCompany.fit_score.desc().nullslast(),
        TargetCompany.heuristic_score.desc().nullslast(),
    )
    if limit:
        stmt = stmt.limit(limit)

    wb = Workbook()
    ws = wb.active
    ws.title = "companies"
    ws.append(COMPANY_COLUMNS)

    rows = session.scalars(stmt).all()
    for c in rows:
        ws.append(
            [
                c.id,
                c.name,
                c.domain,
                c.website,
                c.linkedin_url,
                c.industry,
                c.employee_count,
                c.employee_count_range,
                c.country,
                c.city,
                c.stage,
                c.heuristic_score,
                c.tier_pre,
                c.business_model,
                c.fit_tier,
                c.fit_score,
                c.fit_reason,
                (c.description or "")[:1000],
                ", ".join(c.sources or []),
                c.review_decision,
                c.review_notes,
            ]
        )

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return len(rows)


def export_contacts(session: Session, *, path: str) -> int:
    """Export all contacts (with company name) to an xlsx."""
    wb = Workbook()
    ws = wb.active
    ws.title = "contacts"
    ws.append(CONTACT_COLUMNS)

    stmt = (
        select(CompanyContact, TargetCompany.name)
        .join(TargetCompany, TargetCompany.id == CompanyContact.company_id)
        .order_by(CompanyContact.company_id, CompanyContact.role_category)
    )
    count = 0
    for contact, company_name in session.execute(stmt):
        ws.append(
            [
                contact.id,
                contact.company_id,
                company_name,
                contact.full_name,
                contact.jobtitle,
                contact.role_category,
                contact.seniority,
                contact.linkedin_url,
                contact.email,
                contact.email_status,
                contact.phone,
                contact.source,
            ]
        )
        count += 1

    Path(path).parent.mkdir(parents=True, exist_ok=True)
    wb.save(path)
    return count


def import_review(session: Session, *, path: str) -> dict[str, int]:
    """Re-read an exported xlsx and persist ``review_decision``/``review_notes``.

    Only updates rows with a valid ``id`` and a recognized ``review_decision``. Marks
    ``reviewed_at``. Output: counters ``{updated, dropped, skipped}``.
    """
    wb = load_workbook(path, read_only=True, data_only=True)
    ws = wb.active
    header = [str(c.value).strip() if c.value is not None else "" for c in next(ws.iter_rows(max_row=1))]
    try:
        idx_id = header.index("id")
        idx_decision = header.index("review_decision")
    except ValueError as exc:
        raise ValueError(f"xlsx missing 'id'/'review_decision' columns: {exc}") from exc
    idx_notes = header.index("review_notes") if "review_notes" in header else None

    stats = {"updated": 0, "dropped": 0, "skipped": 0}
    now = datetime.now(UTC)
    for row in ws.iter_rows(min_row=2):
        raw_id = row[idx_id].value if idx_id < len(row) else None
        decision = row[idx_decision].value if idx_decision < len(row) else None
        decision = str(decision).strip().lower() if decision is not None else ""
        if raw_id is None or decision not in REVIEW_DECISIONS:
            stats["skipped"] += 1
            continue
        company = session.get(TargetCompany, int(raw_id))
        if company is None:
            stats["skipped"] += 1
            continue
        company.review_decision = decision
        if idx_notes is not None and idx_notes < len(row):
            note = row[idx_notes].value
            company.review_notes = str(note).strip() if note is not None else company.review_notes
        company.reviewed_at = now
        if decision == "drop":
            company.stage = "dropped"
            company.stage_updated_at = now
            stats["dropped"] += 1
        stats["updated"] += 1
    session.commit()
    return stats
