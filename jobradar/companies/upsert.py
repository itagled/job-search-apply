"""Idempotent upsert of ``target_company`` and ``company_contact``.

Follows the contract of ``jobradar/db/upsert.py`` (neutral dataclass + select-then-insert/update
without overwriting with ``None``). Company dedup by normalized domain and, failing that,
by normalized name. Contact dedup by ``(company_id, linkedin_url)``.
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import func, insert, select
from sqlalchemy.orm import Session

from jobradar.companies.normalize import normalize_domain, normalize_name
from jobradar.db.models import CompanyContact, TargetCompany
from jobradar.db.upsert import UpsertStats


@dataclass(frozen=True)
class NormalizedCompany:
    """Neutral form of a company ready to persist."""

    name: str
    domain: str | None = None
    website: str | None = None
    linkedin_url: str | None = None
    industry: str | None = None
    employee_count: int | None = None
    employee_count_range: str | None = None
    description: str | None = None
    country: str | None = None
    city: str | None = None
    founded_year: int | None = None
    source: str = "unknown"
    source_metadata: dict[str, Any] = field(default_factory=dict)


@dataclass(frozen=True)
class NormalizedContact:
    """Neutral form of a contact (person) linked to a company."""

    full_name: str | None = None
    first_name: str | None = None
    last_name: str | None = None
    jobtitle: str | None = None
    seniority: str | None = None
    role_category: str | None = None
    linkedin_url: str | None = None
    email: str | None = None
    email_status: str | None = None
    phone: str | None = None
    source: str = "unknown"
    source_metadata: dict[str, Any] = field(default_factory=dict)


def _find_existing_company(session: Session, *, domain: str | None, name_norm: str | None) -> TargetCompany | None:
    """Find existing company by domain (case-insensitive) and then by name."""
    if domain:
        row = session.scalar(select(TargetCompany).where(func.lower(TargetCompany.domain) == domain.lower()))
        if row is not None:
            return row
    if name_norm:
        row = session.scalar(select(TargetCompany).where(TargetCompany.name_normalized == name_norm))
        if row is not None:
            return row
    return None


def ingest_company(session: Session, *, company: NormalizedCompany, stats: UpsertStats) -> tuple[int, bool]:
    """Insert or refresh a company. Idempotent by normalized domain/name.

    Output: ``(target_company_id, True if insert / False if update)``.

    On update, only empty fields are filled (no overwriting with ``None`` nor
    overwriting existing firmographics); the source is added to ``sources`` and
    ``source_metadata`` is merged. ``stage`` and review fields are not touched.
    """
    domain = normalize_domain(company.domain) or normalize_domain(company.website)
    name_norm = normalize_name(company.name)
    existing = _find_existing_company(session, domain=domain, name_norm=name_norm)

    if existing is None:
        row = TargetCompany(
            name=company.name.strip(),
            name_normalized=name_norm,
            domain=domain,
            website=company.website,
            linkedin_url=company.linkedin_url,
            industry=company.industry,
            employee_count=company.employee_count,
            employee_count_range=company.employee_count_range,
            description=company.description,
            country=company.country,
            city=company.city,
            founded_year=company.founded_year,
            sources=[company.source],
            first_source=company.source,
            source_metadata=dict(company.source_metadata or {}),
            stage="raw",
        )
        session.add(row)
        session.flush()
        stats.inserted += 1
        return int(row.id), True

    # Update: fill gaps without overwriting existing data.
    if not existing.domain and domain:
        existing.domain = domain
    existing.website = existing.website or company.website
    existing.linkedin_url = existing.linkedin_url or company.linkedin_url
    existing.industry = existing.industry or company.industry
    existing.employee_count = existing.employee_count or company.employee_count
    existing.employee_count_range = existing.employee_count_range or company.employee_count_range
    existing.description = existing.description or company.description
    existing.country = existing.country or company.country
    existing.city = existing.city or company.city
    existing.founded_year = existing.founded_year or company.founded_year
    if not existing.name_normalized and name_norm:
        existing.name_normalized = name_norm

    sources = list(existing.sources or [])
    if company.source and company.source not in sources:
        sources.append(company.source)
        existing.sources = sources
    if company.source_metadata:
        merged = dict(existing.source_metadata or {})
        merged.update(company.source_metadata)
        existing.source_metadata = merged

    stats.updated += 1
    return int(existing.id), False


def bulk_ingest_companies(
    session: Session,
    companies: Iterable[NormalizedCompany],
    *,
    source: str,
    batch_size: int = 500,
) -> UpsertStats:
    """Fast bulk insert of new companies (for large extractions).

    Preloads existing keys (domain and normalized name) into memory and
    does batch INSERTs, avoiding the per-row select of the normal upsert. Does not
    update existing rows (only inserts new ones); fine dedup between
    sources is resolved with the normal upsert in small runs.

    Input: iterable of ``NormalizedCompany``. Output: ``UpsertStats``.
    """
    stats = UpsertStats()

    existing_domains: set[str] = set()
    existing_names: set[str] = set()
    for dom, nm in session.execute(select(func.lower(TargetCompany.domain), TargetCompany.name_normalized)):
        if dom:
            existing_domains.add(dom)
        if nm:
            existing_names.add(nm)

    batch: list[dict[str, Any]] = []

    def flush() -> None:
        if not batch:
            return
        session.execute(insert(TargetCompany), batch)
        session.commit()
        batch.clear()

    for company in companies:
        domain = normalize_domain(company.domain) or normalize_domain(company.website)
        name_norm = normalize_name(company.name)
        dom_key = domain.lower() if domain else None
        # Skip if already exists (by domain, or by name when there is no domain).
        if dom_key and dom_key in existing_domains:
            stats.skipped += 1
            continue
        if not dom_key and name_norm and name_norm in existing_names:
            stats.skipped += 1
            continue
        if dom_key:
            existing_domains.add(dom_key)
        elif name_norm:
            existing_names.add(name_norm)

        batch.append(
            {
                "name": company.name.strip(),
                "name_normalized": name_norm,
                "domain": domain,
                "website": company.website,
                "linkedin_url": company.linkedin_url,
                "industry": company.industry,
                "employee_count": company.employee_count,
                "employee_count_range": company.employee_count_range,
                "description": company.description,
                "country": company.country,
                "city": company.city,
                "founded_year": company.founded_year,
                "sources": [company.source or source],
                "first_source": company.source or source,
                "source_metadata": dict(company.source_metadata or {}),
                "stage": "raw",
            }
        )
        stats.inserted += 1
        if len(batch) >= batch_size:
            flush()
    flush()
    return stats


def ingest_contact(
    session: Session, *, company_id: int, contact: NormalizedContact, stats: UpsertStats
) -> tuple[int | None, bool]:
    """Insert or refresh a company contact.

    Dedup by ``(company_id, linkedin_url)`` when there is a URL; otherwise, by
    ``(company_id, full_name)``. Returns ``(id, inserted?)`` or ``(None, False)``
    if there is no minimal data (neither URL nor name).
    """
    linkedin = (contact.linkedin_url or "").strip() or None
    full_name = (contact.full_name or "").strip() or None
    if not linkedin and not full_name:
        stats.skipped += 1
        return None, False

    existing: CompanyContact | None = None
    if linkedin:
        existing = session.scalar(
            select(CompanyContact).where(
                CompanyContact.company_id == company_id,
                func.lower(CompanyContact.linkedin_url) == linkedin.lower(),
            )
        )
    if existing is None and full_name:
        existing = session.scalar(
            select(CompanyContact).where(
                CompanyContact.company_id == company_id,
                func.lower(CompanyContact.full_name) == full_name.lower(),
            )
        )

    if existing is None:
        row = CompanyContact(
            company_id=company_id,
            full_name=full_name,
            first_name=contact.first_name,
            last_name=contact.last_name,
            jobtitle=contact.jobtitle,
            seniority=contact.seniority,
            role_category=contact.role_category,
            linkedin_url=linkedin,
            email=contact.email,
            email_status=contact.email_status,
            phone=contact.phone,
            source=contact.source,
            source_metadata=dict(contact.source_metadata or {}),
        )
        session.add(row)
        session.flush()
        stats.inserted += 1
        return int(row.id), True

    existing.full_name = existing.full_name or full_name
    existing.first_name = existing.first_name or contact.first_name
    existing.last_name = existing.last_name or contact.last_name
    existing.jobtitle = existing.jobtitle or contact.jobtitle
    existing.seniority = existing.seniority or contact.seniority
    existing.role_category = existing.role_category or contact.role_category
    existing.linkedin_url = existing.linkedin_url or linkedin
    existing.email = existing.email or contact.email
    existing.email_status = existing.email_status or contact.email_status
    existing.phone = existing.phone or contact.phone
    if contact.source_metadata:
        merged = dict(existing.source_metadata or {})
        merged.update(contact.source_metadata)
        existing.source_metadata = merged
    stats.updated += 1
    return int(existing.id), False
