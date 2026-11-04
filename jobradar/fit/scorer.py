"""Relevance score of a vacancy against the target profile.

The problem is the inverse of a candidate-to-role fit: filter MANY vacancies for ONE person with
a fixed target. So the score measures **how well the VACANCY fits what the candidate is looking
for**, evaluating the JOB POSTING. The CV enters ONLY as context to understand which flavor of
the role fits them; the candidate's competitiveness is NOT evaluated.

The rubric comes from the profile (``[scoring]`` in ``profile/target.md``): the LLM scores each
dimension 0-100 independently and Python computes the final score with the profile's weights.
The "identity" dimensions (is this really the role?) act as a continuous multiplier with a floor,
not as one more term of the sum, and the eligibility answer (can the candidate take it from where
they live?) multiplies the result.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jobradar import config, llm, profile

TIER_HIGH = 70
TIER_MEDIUM = 45


def _system_prompt(role: str, rubric: profile.Rubric) -> str:
    dims = "\n".join(f'- "{d.key}": {d.question}' for d in rubric.dimensions)
    cv_dims = ", ".join(f"'{d.key}'" for d in rubric.dimensions if d.uses_cv)
    exception = (
        f"ONLY EXCEPTION: {cv_dims} DOES look at the candidate's profile, but only to judge whether the formal "
        "REQUIREMENTS of the job (degree, kind of experience) are accessible to them, not their overall "
        "competitiveness.\n\n"
        if cv_dims
        else ""
    )
    return (
        f"The candidate is ACTIVELY looking for {role} roles. You get:\n"
        "1) TARGET: the definition of the kind of role they want.\n"
        "2) The candidate's CV: ONLY as context to understand which flavor of the role fits them. It is NOT to "
        "evaluate the candidate.\n"
        "3) VACANCY: a concrete job posting.\n\n"
        "YOUR TASK: score how well the VACANCY fits what the candidate is looking for. You evaluate the POSTING, "
        "not the candidate. Do NOT score the candidate's competitiveness or skills, and do NOT assume that because "
        "the candidate knows something (e.g. LLMs) the vacancy is about it: score each dimension 0-100 BASED ON WHAT "
        "THE JOB DESCRIPTION SAYS. If the posting gives no evidence for a dimension, score it low.\n"
        + exception
        + (rubric.focus + "\n\n" if rubric.focus else "")
        + "Dimensions to score (0-100, on the vacancy):\n"
        + dims
        + "\n\n"
        "Also answer the eligibility: " + rubric.eligibility_question + "\n\n"
        "Also extract from the description the NAME of the company that publishes the vacancy (the employer, not "
        "the recruiting agency if they can be told apart). If it cannot be determined, leave an empty string.\n\n"
        "Answer ONLY with a valid JSON object (no markdown):\n"
        "{\n"
        + ",\n".join(
            f'  "{d.key}": {{"score": 0-100, "reason": "1 sentence about the vacancy"}}' for d in rubric.dimensions
        )
        + ',\n  "eligible": "yes | no | unclear",'
        '\n  "eligible_reason": "1 sentence quoting what the posting says about where to live or work",'
        '\n  "company_name": "name of the employer, or \'\' if it cannot be identified",'
        '\n  "summary": "2-3 sentences: why this vacancy fits or not what the candidate is looking for",'
        '\n  "red_flags": "signals that it is NOT the target role, or \'\' if there are none"\n}'
    )


def _user_prompt(cv: str, target: str, job: dict[str, Any]) -> str:
    desc = (job.get("job_description_full") or job.get("raw_content") or "")[:6000]
    return (
        "== TARGET: the kind of role the candidate is looking for ==\n" + target.strip() + "\n\n"
        "== The candidate's CV (ONLY context for the flavor of the role; do NOT evaluate the candidate) ==\n"
        + cv.strip()[:6000]
        + "\n\n"
        "== VACANCY TO EVALUATE ==\n"
        f"Title: {job.get('title') or '(unknown)'}\n"
        f"Company: {job.get('company') or '(unknown)'}\n"
        f"Location: {job.get('location') or '(unknown)'}\n"
        f"URL: {job.get('post_url') or ''}\n"
        f"Description:\n{desc or '(no description)'}\n"
    )


@dataclass
class FitResult:
    fit_score: int | None
    fit_tier: str | None
    fit_json: dict[str, Any] = field(default_factory=dict)
    company_name: str | None = None
    error: str | None = None


def _score_of(entry: Any) -> float | None:
    """The score of a dimension: dict ``{"score": n}`` or a bare number."""
    if isinstance(entry, dict):
        entry = entry.get("score")
    try:
        return float(entry)
    except (TypeError, ValueError):
        return None


def _unwrap(raw: dict[str, Any], keys: set[str]) -> dict[str, Any]:
    """The dict that actually carries the dimensions (unwrap one level)."""
    if any(k in raw for k in keys):
        return raw
    for alt in ("dimensions", "scores", "criteria"):
        if isinstance(raw.get(alt), dict):
            return raw[alt]
    # Some models wrap everything in an arbitrary key (e.g. ".json").
    for value in raw.values():
        if isinstance(value, dict) and any(k in value for k in keys):
            return value
    return raw


def _normalize_dims(raw: dict[str, Any], rubric: profile.Rubric) -> dict[str, Any]:
    """Resolve the dimensions container and normalize each one to a dict with a score."""
    container = _unwrap(raw, set(rubric.weights))
    dims: dict[str, Any] = {}
    for key in rubric.weights:
        entry = container.get(key)
        score = _score_of(entry)
        if score is None:
            continue
        reason = entry.get("reason") if isinstance(entry, dict) else None
        dims[key] = {"score": score, "reason": reason}
    return dims


def _weighted(dims: dict[str, Any], weight_map: dict[str, float]) -> float | None:
    """Σ(score·weight)/Σ(weight) over the keys of ``weight_map`` with a valid score."""
    num = den = 0.0
    for key, weight in weight_map.items():
        score = _score_of(dims.get(key))
        if score is None or not (0 <= score <= 100):
            continue
        num += score * weight
        den += weight
    if den == 0:
        return None
    return num / den


def _tier(score: float) -> str:
    if score >= TIER_HIGH:
        return "high"
    if score >= TIER_MEDIUM:
        return "medium"
    return "low"


def combine(dims: dict[str, Any], eligible: str, rubric: profile.Rubric) -> dict[str, float] | None:
    """Final score from the dimensions (0-100) and the eligibility answer.

    Shared by the LLM scorer and the Jev pre-filter (``jobradar/fit/jev.py``) so both use the
    same formula. None if there are no valid base dimensions.
    """
    base = _weighted(dims, {k: w for k, w in rubric.weights.items() if k not in rubric.identity})
    if base is None:
        return None
    identity = _weighted(dims, rubric.identity)
    if identity is None:
        identity = base  # conservative fallback if both are missing
    identity_factor = rubric.identity_floor + (1.0 - rubric.identity_floor) * (identity / 100.0)
    eligibility_factor = rubric.eligibility_factors.get(eligible, rubric.eligibility_factors["unclear"])
    return {
        "base": base,
        "identity": identity,
        "identity_factor": identity_factor,
        "eligibility_factor": eligibility_factor,
        "final": base * identity_factor * eligibility_factor,
    }


def score_job(
    cv: str, target: str, job: dict[str, Any], *, model: str | None = None, prof: profile.Profile | None = None
) -> FitResult:
    """Score a vacancy against the target. Soft-fail: returns ``error`` instead of raising."""
    model = model or config.get("FIT_MODEL")
    prof = prof or profile.load()
    rubric = prof.scoring
    try:
        # ``caching``: NanoGPT caches the shared prefix (system + CV + target; OpenRouter does it on its
        # own); only the job description, which goes at the end, is charged in full on each call.
        raw = llm.chat_json(
            _system_prompt(prof.role, rubric), _user_prompt(cv, target, job), model=model, extra={"caching": True}
        ).data
    except llm.OutOfCreditsError:
        raise
    except Exception as exc:  # noqa: BLE001 - soft-fail to not break the batch
        return FitResult(None, None, {}, error=f"llm_error: {exc}")

    dims = _normalize_dims(raw, rubric)
    # The summary/company/eligibility live next to the dimensions if the model wrapped everything.
    meta = _unwrap(raw, set(rubric.weights))

    def field_of(key: str) -> str:
        return str(meta.get(key) or raw.get(key) or "").strip()

    eligible = field_of("eligible").lower()
    if eligible not in rubric.eligibility_factors:
        eligible = "unclear"
    c = combine(dims, eligible, rubric)
    if c is None:
        return FitResult(None, None, {"raw": raw}, error="no valid dimensions")
    final = round(c["final"])
    company = field_of("company_name") or None
    fit_json = {
        "model": model,
        "approach": "job_vs_target_multiplicative",
        "weights": rubric.weights,
        "dimensions": dims,
        "base_score": round(c["base"], 2),
        "identity": round(c["identity"], 2),
        "identity_factor": round(c["identity_factor"], 3),
        "eligible": eligible,
        "eligible_reason": field_of("eligible_reason") or None,
        "eligibility_factor": c["eligibility_factor"],
        "final_score": round(c["final"], 2),
        "company_name": company,
        "summary": field_of("summary") or None,
        "red_flags": field_of("red_flags") or None,
    }
    return FitResult(final, _tier(final), fit_json, company_name=company)
