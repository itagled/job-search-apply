"""S2: complete each company's data (firmographics, description, and LinkedIn page).

Only fill gaps (``COALESCE``): never overwrite existing data.
"""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from jobradar.companies.enrich import enrich_company
from jobradar.companies.serp import fetch_company_linkedin_url, fetch_description_serp
from jobradar.companies.stages.common import ACTIVE, PROMOTED, ids_top, now, process, read, tiers_from
from jobradar.db.models import TargetCompany

log = logging.getLogger(__name__)


def enrich_base(
    *, limit: int | None = None, min_tier: str | None = None, serper: bool = False, workers: int = 10
) -> dict[str, int]:
    """S2: industry, size, description, LinkedIn, and country from Apollo (free) and, with ``serper``,
    from Serper (paid) when Apollo doesn't find the company. Leaves it in ``base_enriched``."""
    stmt = select(TargetCompany.id, TargetCompany.name, TargetCompany.domain, TargetCompany.linkedin_url).where(
        TargetCompany.stage.in_(["raw", "screened"]), *ACTIVE
    )
    if min_tier:
        stmt = stmt.where(TargetCompany.tier_pre.in_(tiers_from(min_tier)))
    work = read(stmt, limit)
    log.info("enrich-base: %d companies, %d threads (serper: %s)", len(work), workers, serper)

    def process_one(w: Any) -> tuple[int, Any]:
        return w.id, enrich_company(name=w.name, domain=w.domain, linkedin_url=w.linkedin_url, use_serper=serper)

    def save(session: Session, rows: list[tuple[int, Any]]) -> None:
        for company_id, d in rows:
            # ``domain`` is not touched: it's the unique dedup key and changing it may conflict with another row.
            session.execute(
                update(TargetCompany)
                .where(TargetCompany.id == company_id)
                .values(
                    industry=func.coalesce(TargetCompany.industry, d.industry),
                    employee_count=func.coalesce(TargetCompany.employee_count, d.employee_count),
                    employee_count_range=func.coalesce(TargetCompany.employee_count_range, d.employee_count_range),
                    description=func.coalesce(TargetCompany.description, d.description),
                    linkedin_url=func.coalesce(TargetCompany.linkedin_url, d.linkedin_url),
                    country=func.coalesce(TargetCompany.country, d.country),
                    stage="base_enriched",
                    stage_updated_at=now(),
                )
            )

    return process("enrich-base", work, process_one, save, workers)


def enrich_serp(
    *, limit: int | None = None, workers: int = 8, source: str | None = None, allow_no_domain: bool = False
) -> dict[str, int]:
    """Description from Google Search (cheap Apify actor) for companies that don't have one."""
    stmt = select(TargetCompany.id, TargetCompany.name, TargetCompany.domain).where(
        TargetCompany.description.is_(None), *ACTIVE
    )
    if not allow_no_domain:
        stmt = stmt.where(TargetCompany.domain.is_not(None))
    if source:
        stmt = stmt.where(TargetCompany.first_source == source)
    work = read(stmt, limit)
    log.info("enrich-serp: %d companies, %d threads", len(work), workers)

    def process_one(w: Any) -> tuple[int, str] | None:
        description = fetch_description_serp(w.name, w.domain)
        return (w.id, description) if description else None

    def save(session: Session, rows: list[tuple[int, str]]) -> None:
        for company_id, description in rows:
            session.execute(
                update(TargetCompany)
                .where(TargetCompany.id == company_id)
                .values(description=func.coalesce(TargetCompany.description, description), updated_at=now())
            )

    return process("enrich-serp", work, process_one, save, workers)


def backfill_company_linkedin(
    *, top: int | None = None, order: str = "fit", limit: int | None = None, workers: int = 8
) -> dict[str, int]:
    """LinkedIn page for the promoted companies that don't have it (Google Search on Apify)."""
    stmt = select(TargetCompany.id, TargetCompany.name, TargetCompany.domain).where(
        PROMOTED, TargetCompany.linkedin_url.is_(None)
    )
    if top:
        stmt = stmt.where(TargetCompany.id.in_(ids_top(top, order)))
    work = read(stmt, limit)
    log.info("backfill-company-linkedin: %d companies without linkedin_url, %d threads", len(work), workers)

    def process_one(w: Any) -> tuple[int, str] | None:
        url = fetch_company_linkedin_url(w.name, w.domain)
        return (w.id, url) if url else None

    def save(session: Session, rows: list[tuple[int, str]]) -> None:
        for company_id, url in rows:
            session.execute(
                update(TargetCompany)
                .where(TargetCompany.id == company_id, TargetCompany.linkedin_url.is_(None))
                .values(linkedin_url=url, updated_at=now())
            )

    return process("backfill-company-linkedin", work, process_one, save, workers)
