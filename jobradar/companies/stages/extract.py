"""S0: populate ``target_company`` from Apollo and the curated seed."""

from __future__ import annotations

from typing import Any

from jobradar import profile
from jobradar.companies import sources
from jobradar.companies.upsert import bulk_ingest_companies, ingest_company
from jobradar.db.session import get_session
from jobradar.db.upsert import UpsertStats

SOURCES = ("apollo", "curated", "all")


def extract(
    *,
    source: str = "all",
    emp_min: int = 5,
    emp_max: int = 100,
    location: str | None = None,
    target: int | None = None,
    dry_run: bool = False,
) -> dict[str, Any]:
    """Fetch companies from each source and leave them in the ``raw`` stage; duplicates are merged."""
    summary: dict[str, Any] = {}
    with get_session() as session:
        if source in ("apollo", "all"):
            place = location or profile.load().companies.search_location
            if not place:
                raise profile.ProfileError("set [companies] country in the profile, or pass --location")
            # Apollo brings thousands: bulk insert.
            companies = sources.fetch_from_apollo_search(
                emp_min=emp_min,
                emp_max=emp_max,
                target=target,
                locations=[place],
            )
            apollo = (
                {"seen": sum(1 for _ in companies)}
                if dry_run
                else bulk_ingest_companies(session, companies, source="apollo_search").as_dict()
            )
            summary["apollo"] = {**apollo, "range": f"{emp_min}-{emp_max}"}

        # The curated seed is short: it is saved one by one, merging sources.
        if source in ("curated", "all") and not dry_run:
            stats = UpsertStats()
            for company in sources.curated_seed():
                ingest_company(session, company=company, stats=stats)
            session.commit()
            summary["curated"] = stats.as_dict()
    return summary
