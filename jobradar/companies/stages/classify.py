"""S4: classify with the LLM whether the company is a startup and how well it fits the profile."""

from __future__ import annotations

import logging
from typing import Any

from sqlalchemy import select, update
from sqlalchemy.orm import Session

from jobradar import config, llm
from jobradar.companies.classify_llm import classify_company_llm
from jobradar.companies.industry_groups import map_industry_group
from jobradar.companies.stages.common import ACTIVE, Failure, now, process, read
from jobradar.db.models import TargetCompany

log = logging.getLogger(__name__)


def classify(*, limit: int | None = None, workers: int = 8) -> dict[str, Any]:
    """S4: startup yes/no, type, business model and fit (``CLASSIFY_MODEL``).

    Only companies with a description and still unclassified; those that fail are left for the next
    pass. Classified ones move to ``classified``.
    """
    stmt = select(
        TargetCompany.id,
        TargetCompany.name,
        TargetCompany.industry_group,
        TargetCompany.industry,
        TargetCompany.employee_count,
        TargetCompany.founded_year,
        TargetCompany.description,
    ).where(TargetCompany.description.is_not(None), TargetCompany.is_startup.is_(None), *ACTIVE)
    work = read(stmt, limit)
    log.info("classify: %d companies, model %s, %d threads", len(work), config.get("CLASSIFY_MODEL"), workers)

    def process_one(w: Any) -> tuple[int, Any] | Failure:
        res = classify_company_llm(
            name=w.name,
            industry=w.industry,
            industry_group=w.industry_group or map_industry_group(w.industry),
            employee_count=w.employee_count,
            founded_year=w.founded_year,
            description=w.description,
        )
        return Failure(f"{w.name}: {res.error}") if res.error else (w.id, res)

    def save(session: Session, rows: list[tuple[int, Any]]) -> None:
        for company_id, r in rows:
            session.execute(
                update(TargetCompany)
                .where(TargetCompany.id == company_id)
                .values(
                    is_startup=r.is_startup,
                    company_type=r.company_type,
                    business_model=r.business_model,
                    fit_tier=r.fit_tier,
                    fit_score=r.fit_score,
                    fit_reason=r.reason,
                    stage="classified",
                    stage_updated_at=now(),
                )
            )

    return {**process("classify", work, process_one, save, workers, warm_up=True), "llm": llm.usage_total()}
