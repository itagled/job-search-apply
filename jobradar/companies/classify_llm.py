"""Company classification (startup or not, fit with the ideal employer) with a cheap LLM.

The system prompt is built from the profile (``[companies]`` in ``profile/target.md``) and is the
same in every call of a run, so the provider caches it: each company only pays for its own data.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jobradar import config, llm, profile

_PROMPT = """\
You classify companies in {country} for someone looking for {role} roles. Your task: decide whether a \
company is a STARTUP and how well it fits the ideal employer.

DEFINITIONS
- STARTUP: a young company (usually founded in the last ~15 years) with a scalable technology or digital \
product, built to grow fast (SaaS, fintech, marketplace, app, platform). It usually talks about "platform", \
"software", "app", "digital", technology.
- NOT A STARTUP: a traditional company (physical services such as installation, assembly, maintenance, \
machinery rental; construction; long-standing manufacturing; classic consulting or outsourcing), a traditional \
small business, an institution (school, university, NGO, government) or a large established corporation.

IDEAL EMPLOYER
{ideal}

FIT (fit_tier)
{fit_guide}

Answer ONLY with a valid JSON object, without extra text, with the keys:
"is_startup" (true/false),
"company_type" (one of: startup, scaleup, traditional, smb, corporate, institution, other),
"business_model" (one of: b2c, b2b_smb, b2b_midmarket, b2b_enterprise, marketplace, other),
"fit_tier" (one of: high, medium, low),
"fit_score" (integer 0-100),
"reason" (1-2 concrete sentences)."""

_DEFAULT_GUIDE = (
    "- high: clearly matches the ideal employer.\n"
    "- low: clearly does not (for example, the kinds of companies the ideal employer excludes).\n"
    "- medium: in between or ambiguous."
)


def system_prompt() -> str:
    prof = profile.load()
    c = prof.companies
    return _PROMPT.format(
        country=c.country or "any country",
        role=prof.role,
        ideal=c.ideal or prof.description[:1500],
        fit_guide=c.fit_guide or _DEFAULT_GUIDE,
    )


@dataclass
class Classification:
    is_startup: bool | None = None
    company_type: str | None = None
    business_model: str | None = None
    fit_tier: str | None = None
    fit_score: int | None = None
    reason: str | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _user_prompt(
    *,
    name: str,
    industry_group: str | None,
    industry: str | None,
    employee_count: int | None,
    founded_year: int | None,
    description: str | None,
) -> str:
    return (
        "Classify this company:\n"
        f"- Name: {name}\n"
        f"- Industry group: {industry_group or 'unknown'}\n"
        f"- Industry (raw): {industry or 'unknown'}\n"
        f"- Employees: {employee_count if employee_count is not None else 'unknown'}\n"
        f"- Founded: {founded_year if founded_year is not None else 'unknown'}\n"
        f"- Description: {description or 'no description'}\n"
    )


def _coerce(raw: dict) -> Classification:
    return Classification(
        is_startup=llm.as_bool(raw.get("is_startup")),
        company_type=(llm.as_text(raw.get("company_type")) or "").lower() or None,
        business_model=(llm.as_text(raw.get("business_model")) or "").lower() or None,
        fit_tier=llm.as_choice(raw.get("fit_tier"), ("high", "medium", "low")),
        fit_score=llm.as_score(raw.get("fit_score")),
        reason=llm.as_text(raw.get("reason")),
    )


def classify_company_llm(
    *,
    name: str,
    industry_group: str | None = None,
    industry: str | None = None,
    employee_count: int | None = None,
    founded_year: int | None = None,
    description: str | None = None,
    model: str | None = None,
    timeout: int = 60,
) -> Classification:
    """Classify a company (startup / fit). Soft-fail: the problem goes in ``error``.

    The system prompt goes first and is the same in every call, so the provider caches it; ``usage``
    shows how many tokens came from that cache.
    """
    user = _user_prompt(
        name=name,
        industry_group=industry_group,
        industry=industry,
        employee_count=employee_count,
        founded_year=founded_year,
        description=description,
    )
    try:
        resp = llm.chat_json(
            system_prompt(),
            user,
            model=model or config.get("CLASSIFY_MODEL"),
            temperature=0,
            max_tokens=300,
            extra=llm.PROMPT_CACHING_1H,
            timeout=timeout,
        )
    except llm.OutOfCreditsError:
        raise
    except Exception as exc:  # noqa: BLE001 - soft-fail to avoid breaking the batch
        return Classification(error=str(exc)[:300])
    result = _coerce(resp.data)
    result.usage = resp.usage
    return result
