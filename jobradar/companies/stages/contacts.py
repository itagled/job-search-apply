"""S5: people of each company (sales, product, founders, talent) from LinkedIn via Apify."""

from __future__ import annotations

import logging
from functools import partial
from typing import Any

from sqlalchemy import nullslast, select, update
from sqlalchemy.orm import Session

from jobradar.companies.contacts_apify import fetch_company_contacts
from jobradar.companies.stages.common import ACTIVE, PROMOTED, ids_top, in_parallel, now, read, tiers_from
from jobradar.companies.upsert import ingest_contact
from jobradar.db.models import TargetCompany
from jobradar.db.session import write_with_retry
from jobradar.db.upsert import UpsertStats

log = logging.getLogger(__name__)


def contacts(
    *, limit: int | None = None, min_fit: str = "high", min_score: int | None = None, workers: int = 6
) -> dict[str, Any]:
    """S5: contacts of startups with fit from ``min_fit`` (and ``min_score``, if provided).

    Each processed company moves to ``contacts_done``, even if no one appears.
    """
    stmt = select(TargetCompany.id, TargetCompany.name, TargetCompany.linkedin_url).where(
        TargetCompany.is_startup.is_(True),
        TargetCompany.fit_tier.in_(tiers_from(min_fit)),
        TargetCompany.linkedin_url.is_not(None),
        TargetCompany.stage.is_distinct_from("contacts_done"),
        *ACTIVE,
    )
    if min_score is not None:
        stmt = stmt.where(TargetCompany.fit_score >= min_score)
    work = read(stmt, limit)
    log.info("contacts: %d companies with fit from %s, %d threads", len(work), min_fit, workers)
    return _search(work, workers=workers, max_items=25, close_stage=True)


def contacts_promoted(
    *, top: int | None = None, order: str = "fit", limit: int | None = None, max_items: int = 25, workers: int = 6
) -> dict[str, Any]:
    """People of the promoted companies, to monitor their posts. Does not move the funnel stage."""
    stmt = (
        select(TargetCompany.id, TargetCompany.name, TargetCompany.linkedin_url)
        .where(PROMOTED, TargetCompany.linkedin_url.is_not(None))
        .order_by(nullslast(TargetCompany.fit_score.desc()))
    )
    if top:
        stmt = stmt.where(TargetCompany.id.in_(ids_top(top, order)))
    work = read(stmt, limit)
    log.info("contacts-promoted: %d companies, %d threads", len(work), workers)
    return _search(work, workers=workers, max_items=max_items, close_stage=False)


def _search(work: list[Any], *, workers: int, max_items: int, close_stage: bool) -> dict[str, Any]:
    """Find the people of each company and save them as they arrive, one company per transaction."""

    def process_one(w: Any) -> tuple[Any, list[Any], float, str]:
        return w, *fetch_company_contacts(w.linkedin_url, max_items=max_items)

    new, cost = 0, 0.0
    for w, contacts, cost_usd, filter_rule in in_parallel(process_one, work, workers):
        new += write_with_retry(partial(_save, company_id=w.id, contacts=contacts, close_stage=close_stage))
        cost += cost_usd
        extra = " (no results with the main filter: Operations added)" if filter_rule == "fallback_ops" else ""
        log.info("%s: %d contacts%s", w.name[:34], len(contacts), extra)
    return {"companies": len(work), "new_contacts": new, "cost_usd": round(cost, 4)}


def _save(session: Session, *, company_id: int, contacts: list[Any], close_stage: bool) -> int:
    stats = UpsertStats()
    for contact in contacts:
        ingest_contact(session, company_id=company_id, contact=contact, stats=stats)
    if close_stage:
        session.execute(
            update(TargetCompany)
            .where(TargetCompany.id == company_id)
            .values(stage="contacts_done", stage_updated_at=now())
        )
    return stats.inserted
