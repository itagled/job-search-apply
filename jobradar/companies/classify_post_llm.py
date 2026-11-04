"""Classification of LinkedIn posts by people at target companies: is it a job opening, and for which role?

A cheap LLM reads the post and returns ``is_job_post``, the role as written and a
``role_bucket`` from the profile's buckets (``[companies] post_buckets``). The system prompt is
the same in every call of a run, so the cost per post is a fraction of a cent.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from jobradar import config, llm, profile

_PROMPT = """\
You read LinkedIn posts written by people at startups and decide whether the post announces a concrete job \
OPENING, and for which role.

WHAT COUNTS AS AN OPENING (is_job_post=true)
- The post looks to hire someone for a concrete role ("we're hiring", "join our team as", "estamos buscando", \
"buscamos un/a ...").
- There must be an identifiable role. A generic employer-branding post without a concrete role does NOT count.

WHAT IS NOT AN OPENING (is_job_post=false)
- Someone announcing that they ARE looking for work or that they were hired.
- Reflections, achievements, product launches, educational content, events, congratulations. When in doubt, \
is_job_post=false.

ROLE NORMALIZATION (role_bucket), choose ONE:
{buckets}

Answer ONLY with a valid JSON object, without extra text, with the keys:
"is_job_post" (true/false),
"detected_role" (the role title as written, cleaned; "" if it is not an opening),
"role_bucket" (one of: {names}; "" if it is not an opening),
"hiring_company" (the hiring company if it is mentioned; "" otherwise),
"confidence" (integer 0-100)."""


def _buckets() -> dict[str, str]:
    return profile.load().companies.post_buckets


def system_prompt() -> str:
    buckets = _buckets()
    return _PROMPT.format(
        buckets="\n".join(f'- "{name}": {desc}' for name, desc in buckets.items()), names=", ".join(buckets)
    )


@dataclass
class PostClassification:
    is_job_post: bool | None = None
    detected_role: str | None = None
    role_bucket: str | None = None
    hiring_company: str | None = None
    confidence: int | None = None
    usage: dict[str, Any] = field(default_factory=dict)
    error: str | None = None


def _coerce(raw: dict) -> PostClassification:
    is_job = llm.as_bool(raw.get("is_job_post"))
    confidence = llm.as_score(raw.get("confidence"))
    if not is_job:
        return PostClassification(is_job_post=is_job, confidence=confidence)
    bucket = (llm.as_text(raw.get("role_bucket")) or "").lower()
    return PostClassification(
        is_job_post=True,
        detected_role=llm.as_text(raw.get("detected_role")),
        role_bucket=bucket if bucket in _buckets() else "other",
        hiring_company=llm.as_text(raw.get("hiring_company")),
        confidence=confidence,
    )


def classify_post_llm(text: str, *, model: str | None = None, timeout: int = 60) -> PostClassification:
    """Classify a post (is it an opening? which role?). Soft-fail: the problem goes in ``error``."""
    if not text or not text.strip():
        return PostClassification(error="empty_text")
    try:
        resp = llm.chat_json(
            system_prompt(),
            f"LinkedIn post:\n{text.strip()}",
            model=model or config.get("CLASSIFY_POST_MODEL"),
            temperature=0,
            max_tokens=300,
            extra=llm.PROMPT_CACHING_1H,
            timeout=timeout,
        )
    except llm.OutOfCreditsError:
        raise
    except Exception as exc:  # noqa: BLE001 - soft-fail to not break the batch
        return PostClassification(error=str(exc)[:300])
    result = _coerce(resp.data)
    result.usage = resp.usage
    return result
