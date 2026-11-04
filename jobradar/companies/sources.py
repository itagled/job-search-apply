"""Company extraction sources (S0).

- ``curated_seed``: known companies from the CSV in the profile's ``[companies] seed_file``
  (name,domain,industry), a quality starting point.
- ``fetch_from_apollo_search``: bulk search of organizations in Apollo by country
  and employee range.
"""

from __future__ import annotations

import csv
import logging
from collections.abc import Iterator
from pathlib import Path

from jobradar import apollo, config, profile
from jobradar.companies.normalize import clean_optional, normalize_domain, normalize_name
from jobradar.companies.upsert import NormalizedCompany

log = logging.getLogger(__name__)


def curated_seed() -> Iterator[NormalizedCompany]:
    """Iterate the companies of the profile's seed CSV (``source='curated'``); none without one."""
    companies = profile.load().companies
    seed = Path(companies.seed_file) if companies.seed_file else None
    if seed and not seed.is_absolute():
        seed = config.ROOT / seed
    if not seed or not seed.is_file():
        return
    with seed.open(encoding="utf-8", newline="") as f:
        rows = [(r.get("name") or "", r.get("domain") or "", r.get("industry") or "") for r in csv.DictReader(f)]
    for name, domain, industry in rows:
        if not domain.strip():
            continue  # placeholder entries / without domain
        yield NormalizedCompany(
            name=name,
            domain=domain,
            industry=industry,
            country=companies.country or None,
            source="curated",
            source_metadata={"seed_industry_hint": industry},
        )


# --------------------------------------------------------------------------- #
# Apollo company search: deep web extraction by location.
# The search returns name/domain/linkedin/city (firmographics filled in S2).
# The 100 results per page are split into `organizations` + `accounts`.
# --------------------------------------------------------------------------- #
def _apollo_org_to_company(o: dict) -> NormalizedCompany | None:
    """Map an Apollo search result to ``NormalizedCompany``."""
    name = clean_optional(o.get("name"))
    if not name:
        return None
    return NormalizedCompany(
        name=name,
        domain=clean_optional(o.get("primary_domain") or o.get("domain")),
        website=clean_optional(o.get("website_url")),
        linkedin_url=clean_optional(o.get("linkedin_url")),
        industry=clean_optional(o.get("industry")),
        employee_count=o.get("estimated_num_employees") if isinstance(o.get("estimated_num_employees"), int) else None,
        employee_count_range=clean_optional(o.get("organization_num_employees_range")),
        description=clean_optional(o.get("short_description")),
        country=clean_optional(o.get("country") or o.get("organization_country")),
        city=clean_optional(o.get("city") or o.get("organization_city")),
        founded_year=o.get("founded_year") if isinstance(o.get("founded_year"), int) else None,
        source="apollo_search",
        source_metadata={"apollo_org_id": o.get("id") or o.get("organization_id")},
    )


# Apollo only allows paginating the first ~10,000 rows per query (partial_results_limit).
# We use a margin to start any sub-range that approaches the limit.
_APOLLO_PAGE_CAP = 9500


def _search(body: dict, timeout: int) -> dict:
    """A page of ``mixed_companies/search``; ``{}`` if Apollo fails (notified and continues)."""
    try:
        return apollo.search_organizations(body, timeout=timeout)
    except apollo.ApolloError as exc:
        log.warning("apollo: %s", exc)
        return {}


def _apollo_count(locations: list[str], emp_range: str, timeout: int = 40) -> int:
    """total_entries for a company query (locations + employee range)."""
    data = _search(
        {
            "organization_locations": locations,
            "organization_num_employees_ranges": [emp_range],
            "page": 1,
            "per_page": 1,
        },
        timeout,
    )
    return int((data.get("pagination") or {}).get("total_entries") or 0)


def _iter_apollo_leaf(
    locations: list[str],
    emp_range: str,
    total: int,
    *,
    seen: set[str],
    per_page: int,
    timeout: int,
) -> Iterator[NormalizedCompany]:
    """Paginate a partition already under the limit and emit unique companies."""
    accessible = min(total, _APOLLO_PAGE_CAP)
    page = 1
    max_pages = (accessible // per_page) + 2
    while page <= max_pages:
        data = _search(
            {
                "organization_locations": locations,
                "organization_num_employees_ranges": [emp_range],
                "page": page,
                "per_page": per_page,
            },
            timeout,
        )
        if not data:
            break
        results = (data.get("organizations") or []) + (data.get("accounts") or [])
        if not results:
            break
        for o in results:
            if not isinstance(o, dict):
                continue
            company = _apollo_org_to_company(o)
            if company is None:
                continue
            key = normalize_domain(company.domain) or normalize_name(company.name)
            if not key or key in seen:
                continue
            seen.add(key)
            yield company
        if page * per_page >= accessible:
            break
        page += 1


def _iter_apollo_range(
    locations: list[str],
    lo: int,
    hi: int,
    *,
    seen: set[str],
    per_page: int,
    timeout: int,
) -> Iterator[NormalizedCompany]:
    """Adaptive partition by employees: bisect [lo,hi] until under the limit."""
    emp_range = f"{lo},{hi}"
    total = _apollo_count(locations, emp_range, timeout)
    if total == 0:
        return
    if total > _APOLLO_PAGE_CAP and lo < hi:
        mid = (lo + hi) // 2
        yield from _iter_apollo_range(locations, lo, mid, seen=seen, per_page=per_page, timeout=timeout)
        yield from _iter_apollo_range(locations, mid + 1, hi, seen=seen, per_page=per_page, timeout=timeout)
        return
    log.info("apollo: range %s has %s companies, paginating", emp_range, total)
    yield from _iter_apollo_leaf(locations, emp_range, total, seen=seen, per_page=per_page, timeout=timeout)


def fetch_from_apollo_search(
    *,
    locations: list[str],
    emp_min: int = 5,
    emp_max: int = 100,
    target: int | None = None,
    per_page: int = 100,
    timeout: int = 40,
) -> Iterator[NormalizedCompany]:
    """Extract companies from Apollo by employee range, partitioning under the 10k limit.

    Input: ``emp_min``/``emp_max`` (employee range), optional ``target`` (None = all
    accessible), ``locations``. Requires ``APOLLO_API_KEY``.
    Output: iterator of ``NormalizedCompany`` (``source='apollo_search'``), deduplicated
    within the run by domain or normalized name. Covers all [emp_min,emp_max]
    by splitting the range into sub-queries of <10k and merging results.
    """
    if not apollo.configured():
        log.warning("APOLLO_API_KEY is missing: skipping apollo_search")
        return
    seen: set[str] = set()
    n = 0
    for company in _iter_apollo_range(
        locations, emp_min, emp_max, seen=seen, per_page=max(1, min(per_page, 100)), timeout=timeout
    ):
        yield company
        n += 1
        if target is not None and n >= target:
            break
    log.info("apollo_search: %d unique companies, range %s-%s, target %s", n, emp_min, emp_max, target)
