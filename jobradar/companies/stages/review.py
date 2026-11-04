"""Human review between stages: export to Excel, import decisions, and view the summary.

In the exported .xlsx, ``review_decision`` (keep/promote/hold/drop) is edited; rows with
``hold`` or ``drop`` do not advance to the next stage, and ``promote`` marks the best companies,
the only ones the LinkedIn monitor follows.
"""

from __future__ import annotations

from typing import Any

from sqlalchemy import func, select

from jobradar import config
from jobradar.companies import xlsx_io
from jobradar.db.models import TargetCompany
from jobradar.db.session import get_session

EXPORT_DIR = config.ROOT / "exports"


def export(
    *, stage: str | None = None, path: str | None = None, limit: int | None = None, contacts: bool = False
) -> dict[str, Any]:
    """Companies (from one stage or all) or contacts to an .xlsx for manual review."""
    with get_session() as session:
        if contacts:
            path = path or str(EXPORT_DIR / "contacts.xlsx")
            return {"contacts": xlsx_io.export_contacts(session, path=path), "file": path}
        path = path or str(EXPORT_DIR / f"companies_{stage or 'all'}.xlsx")
        n = xlsx_io.export_companies(session, path=path, stage=stage, limit=limit)
        return {"companies": n, "stage": stage or "all", "file": path}


def import_review(*, path: str) -> dict[str, Any]:
    """Save the decisions (``review_decision``, ``review_notes``) from an already reviewed .xlsx."""
    with get_session() as session:
        return {**xlsx_io.import_review(session, path=path), "file": path}


def stats() -> dict[str, dict[str, int]]:
    """How many companies are in each stage and in each fit tier."""
    with get_session() as session:
        by_stage = session.execute(select(TargetCompany.stage, func.count()).group_by(TargetCompany.stage)).all()
        by_tier = session.execute(
            select(TargetCompany.fit_tier, func.count())
            .where(TargetCompany.fit_tier.is_not(None))
            .group_by(TargetCompany.fit_tier)
        ).all()
    return {"by_stage": dict(by_stage), "by_tier": dict(by_tier)}
