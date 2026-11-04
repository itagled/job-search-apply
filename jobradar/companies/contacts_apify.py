"""People of a company via harvestapi LinkedIn.

Actor ``harvestapi/linkedin-company-employees`` in Short mode ($4/1k), cookieless.
Filters by LinkedIn function (server-side) to pay ONLY for relevant profiles:
- Primary: the profile's ``[companies] contact_functions``.
- Fallback (only if primary returns 0): adds Operations (for Country Managers/GMs).
Location: ``APIFY_EMPLOYEES_LOCATIONS`` or else the profile's country.
"""

from __future__ import annotations

import logging
import re

from jobradar import apify, config, profile
from jobradar.companies.upsert import NormalizedContact

log = logging.getLogger(__name__)

# LinkedIn function ids (see the actor schema), as strings.
LINKEDIN_FUNCTIONS = {
    "Accounting": "1",
    "Administrative": "2",
    "Arts and Design": "3",
    "Business Development": "4",
    "Community and Social Services": "5",
    "Consulting": "6",
    "Education": "7",
    "Engineering": "8",
    "Entrepreneurship": "9",
    "Finance": "10",
    "Healthcare Services": "11",
    "Human Resources": "12",
    "Information Technology": "13",
    "Legal": "14",
    "Marketing": "15",
    "Media and Communication": "16",
    "Military and Protective Services": "17",
    "Operations": "18",
    "Product Management": "19",
    "Program and Project Management": "20",
    "Purchasing": "21",
    "Quality Assurance": "22",
    "Real Estate": "23",
    "Research": "24",
    "Sales": "25",
    "Customer Success and Support": "26",
}


def _function_ids(names: list[str]) -> list[str]:
    unknown = [name for name in names if name not in LINKEDIN_FUNCTIONS]
    if unknown:
        raise profile.ProfileError(f"companies.contact_functions: unknown LinkedIn functions {unknown}")
    return [LINKEDIN_FUNCTIONS[name] for name in names]


def _locations() -> list[str]:
    """``APIFY_EMPLOYEES_LOCATIONS`` or else the profile's country (nothing: no location filter)."""
    country = profile.load().companies.country
    return config.get_list("APIFY_EMPLOYEES_LOCATIONS") or ([country] if country else [])


def _norm_url(u: str | None) -> str:
    u = (u or "").strip()
    if u.startswith("http://"):
        u = "https://" + u[7:]
    return u


def _role_of(title: str | None) -> str:
    """Role category from the job title (for company_contact.role_category)."""
    t = (title or "").lower()
    if any(k in t for k in ("founder", "fundador", "co-founder", "cofounder", "socio", "owner")):
        return "founder"
    if "ceo" in t or "chief executive" in t or "gerente general" in t:
        return "ceo"
    if re.search(r"\b(cro|cso|cmo|coo|cto|cpo|chro)\b", t) or "chief" in t:
        return "c_level"
    if any(
        k in t
        for k in (
            "talent",
            "talento",
            "recruit",
            "reclut",
            "selección",
            "seleccion",
            "people",
            "personas",
            "rrhh",
            "human resources",
            "recursos humanos",
        )
    ):
        return "talent"
    if any(
        k in t
        for k in ("sales", "ventas", "comercial", "revenue", "business development", "account", "partnership", "growth")
    ):
        return "sales"
    if any(k in t for k in ("product", "producto")):
        return "product"
    if any(k in t for k in ("operation", "operacion", "country manager", "general manager", "gerente")):
        return "operations"
    return "other"


def _run_actor(
    company_url: str,
    function_ids: list[str],
    *,
    max_items: int,
    timeout: int,
) -> tuple[list[dict], float]:
    """Run the actor for a company: (items, actual cost in USD). Soft-fail: ([], 0).

    Profiles are stored raw as third-party data.
    """
    if not apify.token():
        return [], 0
    payload = {
        "companies": [company_url],
        "profileScraperMode": "Short ($4 per 1k)",
        "maxItems": max_items,
        "locations": _locations(),
        "functionIds": function_ids,
    }
    try:
        res = apify.run(
            config.get("APIFY_EMPLOYEES_ACTOR") or "",
            payload,
            timeout_secs=max(30.0, timeout),
            accept_partial=True,
            raw_kind="linkedin_employees",
            third_party=True,
        )
    except apify.ApifyError as exc:  # soft-fail to not break the batch
        log.warning("employees of %s: %s", company_url, exc)
        return [], 0
    return res.items, res.cost_usd or 0.0


def _to_contact(it: dict, *, source_tag: str) -> NormalizedContact | None:
    """Map an actor item to ``NormalizedContact``."""
    first = (it.get("firstName") or "").strip() or None
    last = (it.get("lastName") or "").strip() or None
    full = " ".join(x for x in (first, last) if x) or None
    positions = it.get("currentPositions") or []
    pos0 = positions[0] if positions and isinstance(positions[0], dict) else {}
    title = (pos0.get("position") or pos0.get("title") or it.get("headline") or "").strip() or None
    url = (it.get("linkedinUrl") or it.get("profileUrl") or "").strip() or None
    if not url and not full:
        return None
    loc = it.get("location")
    loc_txt = loc.get("linkedinText") if isinstance(loc, dict) else loc
    return NormalizedContact(
        full_name=full,
        first_name=first,
        last_name=last,
        jobtitle=title,
        role_category=_role_of(title),
        linkedin_url=url,
        source="apify_linkedin_employees",
        source_metadata={"location": loc_txt, "company_ln": pos0.get("companyName"), "filter": source_tag},
    )


def fetch_company_contacts(
    linkedin_url: str,
    *,
    max_items: int = 25,
    timeout: int = 240,
) -> tuple[list[NormalizedContact], float, str]:
    """People of a company in the profile's functions; if there are none, adding Operations.

    Output: (contacts, actual cost in USD of the runs, filter_used).
    """
    url = _norm_url(linkedin_url)
    if not url:
        return [], 0.0, "no_url"
    primary = _function_ids(profile.load().companies.contact_functions)
    items, cost = _run_actor(url, primary, max_items=max_items, timeout=timeout)
    used = "primary"
    if not items:
        fallback = [*primary, LINKEDIN_FUNCTIONS["Operations"]]
        items2, cost2 = _run_actor(url, fallback, max_items=max_items, timeout=timeout)
        items, cost, used = items2, cost + cost2, "fallback_ops"
    contacts = [c for c in (_to_contact(it, source_tag=used) for it in items) if c is not None]
    return contacts, cost, used
