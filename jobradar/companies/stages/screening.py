"""S1 and S3: heuristic fit score, no API cost (rules in ``companies.heuristics``)."""

from __future__ import annotations

from collections.abc import Callable

from sqlalchemy import select

from jobradar.companies.heuristics import HeuristicResult, score_cheap, score_complex
from jobradar.companies.stages.common import ACTIVE, now
from jobradar.db.models import TargetCompany
from jobradar.db.session import SessionLocal


def screen(*, limit: int | None = None) -> dict[str, int]:
    """S1: cheap heuristic with what extraction brought (``raw`` → ``screened``)."""
    return _score(score_cheap, since="raw", to="screened", limit=limit)


def rescreen(*, limit: int | None = None) -> dict[str, int]:
    """S3: heuristic that weighs enriched description and size more (``base_enriched`` → ``rescreened``)."""
    return _score(score_complex, since="base_enriched", to="rescreened", limit=limit)


def _score(scorer: Callable[..., HeuristicResult], *, since: str, to: str, limit: int | None) -> dict[str, int]:
    stmt = select(TargetCompany).where(TargetCompany.stage == since, *ACTIVE)
    if limit:
        stmt = stmt.limit(limit)
    # No expiry on commit: so partial commits don't reload every DB row.
    with SessionLocal(expire_on_commit=False) as session:
        companies = session.scalars(stmt).all()
        for i, c in enumerate(companies, 1):
            r = scorer(
                name=c.name,
                industry=c.industry,
                description=c.description,
                employee_count=c.employee_count,
                employee_count_range=c.employee_count_range,
            )
            c.heuristic_score, c.tier_pre, c.heuristic_signals = r.score, r.tier, r.signals
            c.stage, c.stage_updated_at = to, now()
            if i % 500 == 0:
                session.commit()
        session.commit()
    return {"processed": len(companies)}
