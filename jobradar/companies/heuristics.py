"""Heuristic scoring of company fit (no API cost).

Idea: reward the industries and words the profile calls a good fit and penalize the ones it
calls a bad fit (``[companies] good_keywords`` and ``bad_keywords``).

- ``score_cheap`` (S1): on data already present after extraction.
- ``score_complex`` (S3): same but weighting the enriched description and
  the employee size band more heavily.
"""

from __future__ import annotations

from dataclasses import dataclass

from jobradar import profile
from jobradar.text import strip_accents


@dataclass
class HeuristicResult:
    """Result of the heuristic scoring."""

    score: int
    tier: str  # high | medium | low
    signals: dict


def _keyword_hits(haystack: str, keywords: tuple[str, ...]) -> list[str]:
    """Keywords present as substring in the normalized text."""
    return [kw for kw in keywords if kw in haystack]


def _size_adjustment(employee_count: int | None, emp_range: str | None) -> tuple[int, str]:
    """Adjustment by size band; prefers scaleups (11–500)."""
    count = employee_count
    if count is None and emp_range:
        # Range like "11-50": take the lower bound as an approximation.
        digits = "".join(c if c.isdigit() else " " for c in emp_range).split()
        if digits:
            count = int(digits[0])
    if count is None:
        return 0, "unknown"
    if count < 5:
        return -8, "pre_seed"  # too early for sales pipelines
    if count <= 10:
        return 4, "early"
    if count <= 500:
        return 15, "scaleup"  # sweet spot
    if count <= 2000:
        return 4, "mid_enterprise"
    return -8, "enterprise"


def _tier_from_score(score: int) -> str:
    if score >= 68:
        return "high"
    if score >= 50:
        return "medium"
    return "low"


def _score(
    *,
    name: str | None,
    industry: str | None,
    description: str | None,
    employee_count: int | None,
    employee_count_range: str | None,
    description_weight: int,
) -> HeuristicResult:
    """Shared scoring core; ``description_weight`` increases the weight of the description."""
    text_industry = strip_accents(f"{name or ''} {industry or ''}")
    text_desc = strip_accents(description or "")

    companies = profile.load().companies
    good = tuple(strip_accents(k) for k in companies.good_keywords)
    bad = tuple(strip_accents(k) for k in companies.bad_keywords)
    high_industry = _keyword_hits(text_industry, good)
    low_industry = _keyword_hits(text_industry, bad)
    high_desc = _keyword_hits(text_desc, good)
    low_desc = _keyword_hits(text_desc, bad)

    score = 50
    score += min(len(high_industry), 3) * 8
    score -= min(len(low_industry), 3) * 12
    score += min(len(high_desc), 4) * description_weight
    score -= min(len(low_desc), 4) * description_weight

    size_adj, size_band = _size_adjustment(employee_count, employee_count_range)
    score += size_adj

    score = max(0, min(100, score))
    return HeuristicResult(
        score=score,
        tier=_tier_from_score(score),
        signals={
            "high_industry": high_industry,
            "low_industry": low_industry,
            "high_desc": high_desc,
            "low_desc": low_desc,
            "size_band": size_band,
        },
    )


def score_cheap(
    *,
    name: str | None,
    industry: str | None,
    description: str | None = None,
    employee_count: int | None = None,
    employee_count_range: str | None = None,
) -> HeuristicResult:
    """S1: cheap scoring on data already present (little description, lots of industry/name)."""
    return _score(
        name=name,
        industry=industry,
        description=description,
        employee_count=employee_count,
        employee_count_range=employee_count_range,
        description_weight=3,
    )


def score_complex(
    *,
    name: str | None,
    industry: str | None,
    description: str | None = None,
    employee_count: int | None = None,
    employee_count_range: str | None = None,
) -> HeuristicResult:
    """S3: scoring on enriched data (more weight to the description)."""
    return _score(
        name=name,
        industry=industry,
        description=description,
        employee_count=employee_count,
        employee_count_range=employee_count_range,
        description_weight=6,
    )
