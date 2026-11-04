"""Pre-filter of fit scoring with Jev (TypeSafe System One via NanoGPT ``/systemone``).

Jev does not generate text: it receives the posting as ``state`` and a map of typed questions and
returns numbers (a score per level, a choice with probabilities). The dimensions of the profile's
rubric are asked as ``score`` questions of 5 levels (0-4 -> 0-100) and the eligibility as a
``choice``; the score is computed with ``scorer.combine`` (same formula as the LLM scorer).

Use in ``jobradar/fit/run.py``: if the Jev score is below the threshold (default 25) the vacancy
keeps the Jev score and does NOT go to the LLM; otherwise the LLM scorer gives the final score
with explanations. Benchmark 2026-09-28 (384 postings already scored by deepseek): Spearman 0.87;
with threshold 25 about 15% of the real DB passes and 100% of the high (>=70) and >=60 are kept.
Jev is less reliable on eligibility (it tends to 'no'): that is why it only filters and does not
replace the LLM. Cost ~US$0.00009 per posting (input only, US$0.042/MTok).
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jobradar import config, llm, profile
from jobradar.fit.scorer import combine

ELIGIBLE = "eligible"


def questions(rubric: profile.Rubric) -> dict[str, dict[str, Any]]:
    """The rubric as Jev questions: one 5-level score per dimension and the eligibility choice."""
    out: dict[str, dict[str, Any]] = {
        d.key: {"type": "score", "instructions": d.question, "criteria": list(d.levels)} for d in rubric.dimensions
    }
    out[ELIGIBLE] = {
        "type": "choice",
        "instructions": rubric.eligibility_question,
        "criteria": dict(rubric.eligibility_choices),
    }
    return out


def model() -> str:
    return config.get("JEV_MODEL") or ""


def min_score() -> float:
    return config.get_float("FIT_TRIAGE_MIN") or 0.0


@dataclass
class JevResult:
    final: float | None
    fit_json: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def jev_score(job: dict[str, Any], *, prof: profile.Profile | None = None) -> JevResult:
    """Jev score of a vacancy (same 0-100 scale and formula as the scorer). Soft-fail."""
    rubric = (prof or profile.load()).scoring
    desc = (job.get("job_description_full") or job.get("raw_content") or "")[:6000]
    state = {
        "job_title": job.get("title") or "",
        "company": job.get("company") or "",
        "location": job.get("location") or "",
        "description": desc,
    }
    try:
        data = llm.systemone(state, questions(rubric), model=model())
    except llm.OutOfCreditsError:
        raise
    except Exception as exc:  # noqa: BLE001 - soft-fail: the caller decides (goes to the LLM)
        return JevResult(None, error=f"jev_error: {exc}")
    answers = data.get("answers") or {}
    dims = {
        k: {"score": answers[k]["score"] / 4 * 100}
        for k in rubric.weights
        if isinstance(answers.get(k), dict) and answers[k].get("score") is not None
    }
    eligible = str((answers.get(ELIGIBLE) or {}).get("choice") or "unclear")
    c = combine(dims, eligible, rubric)
    if c is None:
        return JevResult(None, error="jev: no valid dimensions")
    fit_json = {
        "model": data.get("model") or model(),
        "approach": "jev_triage",
        "dimensions": {k: {"score": round(v["score"], 1)} for k, v in dims.items()},
        "base_score": round(c["base"], 2),
        "identity": round(c["identity"], 2),
        "identity_factor": round(c["identity_factor"], 3),
        "eligible": eligible,
        "eligible_probs": (answers.get(ELIGIBLE) or {}).get("probabilities"),
        "eligibility_factor": c["eligibility_factor"],
        "final_score": round(c["final"], 2),
        "usage": data.get("usage"),
    }
    return JevResult(c["final"], fit_json)
