"""Firmographic enrichment of companies (S2) via Apollo and Serper.

Strategy: Apollo ``organizations/enrich`` by domain → ``mixed_companies/search``
by name → Serper fallback (knowledge graph + organic results, ``gl=cl``).
"""

from __future__ import annotations

import logging
import re
from collections.abc import Callable
from dataclasses import dataclass, field
from typing import Any

from jobradar import apollo, profile, serper
from jobradar.companies.normalize import clean_optional, normalize_domain

log = logging.getLogger(__name__)


@dataclass
class CompanyEnrichment:
    """Normalized firmographic fields from external providers."""

    domain: str | None = None
    industry: str | None = None
    employee_count: int | None = None
    employee_count_range: str | None = None
    description: str | None = None
    linkedin_url: str | None = None
    country: str | None = None
    trace: list[dict[str, Any]] = field(default_factory=list)


def _parse_employee_count(value: object) -> int | None:
    """Parse an employee count to a positive int, or ``None``."""
    if isinstance(value, bool):
        return None
    if isinstance(value, int):
        return value if value > 0 else None
    if isinstance(value, float):
        return int(value) if value > 0 else None
    if isinstance(value, str):
        digits = re.sub(r"[^\d]", "", value)
        if digits:
            parsed = int(digits)
            return parsed if parsed > 0 else None
    return None


def _apollo_org_to_fields(org: dict[str, Any]) -> CompanyEnrichment:
    """Map an Apollo ``organization`` object to normalized fields."""
    industries = org.get("industries")
    industry = clean_optional(org.get("industry"))
    if industry is None and isinstance(industries, list) and industries:
        first = industries[0]
        industry = (
            clean_optional(first)
            if isinstance(first, str)
            else clean_optional(first.get("name") if isinstance(first, dict) else None)
        )
    country = clean_optional(org.get("country"))
    if country is None:
        raw_address = clean_optional(org.get("raw_address"))
        if raw_address and "," in raw_address:
            country = clean_optional(raw_address.rsplit(",", 1)[-1])
    return CompanyEnrichment(
        domain=normalize_domain(org.get("primary_domain") or org.get("website_url")),
        industry=industry,
        employee_count=_parse_employee_count(org.get("estimated_num_employees") or org.get("employee_count")),
        employee_count_range=clean_optional(
            org.get("employee_count_range") or org.get("organization_num_employees_range")
        ),
        description=clean_optional(org.get("short_description") or org.get("seo_description")),
        linkedin_url=clean_optional(org.get("linkedin_url")),
        country=country,
    )


def _merge_missing(dst: CompanyEnrichment, src: CompanyEnrichment) -> None:
    """Fill in ``dst`` only the empty fields with those from ``src``."""
    for key in (
        "domain",
        "industry",
        "employee_count",
        "employee_count_range",
        "description",
        "linkedin_url",
        "country",
    ):
        if getattr(dst, key) in (None, "") and getattr(src, key):
            setattr(dst, key, getattr(src, key))


def enrich_with_apollo(*, domain: str | None, name: str | None, timeout: int = 20) -> CompanyEnrichment:
    """Enrich a company with Apollo (enrich by domain + search by name)."""
    result = CompanyEnrichment()
    if not apollo.configured():
        result.trace.append({"provider": "apollo", "error": "APOLLO_API_KEY missing"})
        return result

    if domain:
        org = _soft(lambda: apollo.enrich_organization(domain, timeout=timeout))
        if org:
            mapped = _apollo_org_to_fields(org)
            _merge_missing(result, mapped)
            result.trace.append({"provider": "apollo", "strategy": "domain_enrich", "domain": domain})

    missing_core = not any((result.industry, result.description, result.employee_count, result.linkedin_url))
    if missing_core and name:
        data = _soft(
            lambda: apollo.search_organizations(
                {"q_organization_name": name, "page": 1, "per_page": 1}, timeout=timeout
            )
        )
        if data:
            accounts = data.get("accounts") or data.get("organizations") or []
            first = accounts[0] if isinstance(accounts, list) and accounts else {}
            if isinstance(first, dict):
                _merge_missing(result, _apollo_org_to_fields(first))
                result.trace.append({"provider": "apollo", "strategy": "name_search", "name": name})
    return result


def _soft(call: Callable[[], Any]) -> Any:
    """Result of an Apollo call, or ``None`` if it fails (enrichment continues)."""
    try:
        return call()
    except apollo.ApolloError as exc:
        log.warning("apollo: %s", exc)
        return None


def enrich_with_serper(*, name: str, timeout: int = 20) -> CompanyEnrichment:
    """Fill gaps with Serper's knowledge graph and organic results."""
    result = CompanyEnrichment()
    cleaned = clean_optional(name)
    if not cleaned:
        return result
    where = profile.load().companies
    data = serper.search(f"{cleaned} company", gl=where.country_code.lower(), hl=where.language, timeout=timeout) or {}
    knowledge = data.get("knowledgeGraph") if isinstance(data, dict) else None
    organic = data.get("organic") if isinstance(data, dict) else None

    if isinstance(knowledge, dict):
        result.description = result.description or clean_optional(knowledge.get("description"))
        result.industry = result.industry or clean_optional(knowledge.get("type"))
        website = clean_optional(knowledge.get("website"))
        if website:
            result.domain = result.domain or normalize_domain(website)
    if not result.domain and isinstance(organic, list):
        for item in organic:
            if not isinstance(item, dict):
                continue
            host = normalize_domain(clean_optional(item.get("link")))
            if host:
                result.domain = host
                break
    result.trace.append({"provider": "serper", "query": cleaned})
    return result


def enrich_company(
    *,
    name: str,
    domain: str | None = None,
    linkedin_url: str | None = None,
    timeout: int = 20,
    use_serper: bool = False,
) -> CompanyEnrichment:
    """Enrich a company: Apollo first; Serper (paid) only if ``use_serper``.

    Apollo (``organizations/enrich``) does not consume credits, only rate-limit. Serper is
    a separate paid API: off by default to avoid spending without authorization.
    """
    merged = enrich_with_apollo(domain=normalize_domain(domain), name=name, timeout=timeout)
    if linkedin_url and not merged.linkedin_url:
        merged.linkedin_url = linkedin_url

    if use_serper and not all((merged.industry, merged.description, merged.linkedin_url)):
        serper = enrich_with_serper(name=name, timeout=timeout)
        _merge_missing(merged, serper)
        merged.trace.extend(serper.trace)
    return merged
