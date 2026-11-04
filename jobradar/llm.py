"""Single LLM client, for NanoGPT or OpenRouter (OpenAI-compatible APIs): chat with JSON output and Jev.

- The provider is ``LLM_PROVIDER``, or the one whose key is set (NanoGPT first). The same model names
  exist on both. Jev (``/systemone``) is only on NanoGPT: ``has_jev()`` tells the callers.
- Retries with backoff on network failures, 429, 5xx and 529, via ``http_client``. Never on
  401/403: retrying prolongs the block due to invalid credentials.
- ``OutOfCreditsError`` on HTTP 402, to stop the batch instead of failing row by row.
- Disk cache keyed by request hash (``LLM_CACHE``): the same call is not paid twice,
  for example if a batch crashes after calling the LLM and before saving to the DB.
- Raw: every paid response goes to ``data/raw/llm/<date>.jsonl`` with the model, the
  parameters and the input hash. The prompt is not repeated there: the hash is enough to cross-reference it.
- ``usage_total()`` sums calls and tokens of the process, for each CLI summary.
"""

from __future__ import annotations

import json
import logging
import threading
from collections.abc import Callable, Iterable
from dataclasses import dataclass, field
from typing import Any, TypeVar

from jobradar import config, http_client, raw

log = logging.getLogger(__name__)

RETRY_STATUS = http_client.RETRY_STATUS | {529}
# Cache of the system prompt in NanoGPT (must be requested): message 0 stays cached for 1 h,
# so the thousands of calls in a batch reuse the prefix and cost fractions of a cent.
PROMPT_CACHING_1H = {"promptCaching": {"enabled": True, "ttl": "1h", "cutAfterMessageIndex": 0}}
# name -> (label, variable with the key, variable with the base URL)
PROVIDERS = {
    "nanogpt": ("NanoGPT", "NANOGPT_API_KEY", "NANOGPT_BASE_URL"),
    "openrouter": ("OpenRouter", "OPENROUTER_API_KEY", "OPENROUTER_BASE_URL"),
}
T = TypeVar("T")


class LLMError(RuntimeError):
    """The LLM API failed or returned something unusable."""


class OutOfCreditsError(LLMError):
    """The provider responded 402 (no balance). Batches let it pass to stop."""


@dataclass
class LLMResponse:
    data: dict[str, Any]
    usage: dict[str, Any] = field(default_factory=dict)
    model: str = ""
    cached: bool = False


_cache: http_client.DiskCache | None = None
_usage: dict[str, float] = {}
_usage_lock = threading.Lock()


def provider() -> str:
    """``LLM_PROVIDER``, or the provider whose key is set (NanoGPT first)."""
    chosen = (config.get("LLM_PROVIDER") or "").strip().lower()
    if chosen and chosen not in PROVIDERS:
        raise config.ConfigError(f"LLM_PROVIDER must be one of: {', '.join(PROVIDERS)} (got {chosen!r})")
    return chosen or next((name for name, (_, key, _) in PROVIDERS.items() if config.get(key)), "nanogpt")


def has_key() -> bool:
    return bool(config.get(PROVIDERS[provider()][1]))


def require_key() -> str:
    """The provider's key; ``ConfigError`` with both options if it is missing."""
    key = PROVIDERS[provider()][1]
    value = config.get(key)
    if not value:
        raise config.ConfigError(
            f"Missing {key} in .env: the LLM needs a NanoGPT key (https://nano-gpt.com/api) or an "
            "OpenRouter one (https://openrouter.ai/settings/keys); see .env.example"
        )
    return value


def has_jev() -> bool:
    """Jev (``/systemone``) only exists on NanoGPT."""
    return provider() == "nanogpt"


def _disk_cache() -> http_client.DiskCache:
    global _cache
    if _cache is None:
        _cache = http_client.DiskCache(config.DATA_DIR / "cache" / "llm")
    return _cache


def _count(usage: Any, *, cached: bool) -> None:
    usage = usage if isinstance(usage, dict) else {}
    details = usage.get("prompt_tokens_details") or {}
    with _usage_lock:
        _usage["calls"] = _usage.get("calls", 0) + 1
        if cached:
            _usage["local_cache_hits"] = _usage.get("local_cache_hits", 0) + 1
            return
        for key, jev_key in (("prompt_tokens", "input_tokens"), ("completion_tokens", "output_tokens")):
            _usage[key] = _usage.get(key, 0) + (usage.get(key) or usage.get(jev_key) or 0)
        _usage["provider_cached_tokens"] = _usage.get("provider_cached_tokens", 0) + (
            usage.get("cache_read_input_tokens") or details.get("cached_tokens") or 0
        )
        cost = usage.get("cost", usage.get("estimated_cost"))
        if isinstance(cost, (int, float)):
            _usage["cost_usd"] = round(_usage.get("cost_usd", 0) + cost, 6)


def usage_total() -> dict[str, float]:
    """Calls and tokens accumulated in this process."""
    with _usage_lock:
        return dict(_usage)


def _should_retry(resp: Any) -> bool:
    return resp.status_code in RETRY_STATUS and "authentication" not in resp.text[:400].lower()


def _call(
    endpoint: str, payload: dict[str, Any], parse: Callable[[dict], T], *, timeout: float, use_cache: bool
) -> tuple[T, dict, bool]:
    """POST to the provider with cache: returns (parsed result, body, came_from_cache)."""
    key = http_client.DiskCache.key(endpoint, payload)
    use_cache = use_cache and config.get_bool("LLM_CACHE")
    if use_cache:
        hit = _disk_cache().get(key)
        if hit is not None:
            _count(hit.get("usage"), cached=True)
            return parse(hit), hit, True
    label, _, base_url = PROVIDERS[provider()]
    headers = {"Authorization": f"Bearer {require_key()}", "Content-Type": "application/json"}
    url = f"{(config.get(base_url) or '').rstrip('/')}/{endpoint}"
    resp = http_client.post(
        url, json=payload, headers=headers, timeout=timeout, retries=4, backoff=2.0, retry_if=_should_retry
    )
    if resp.status_code == 402:
        raise OutOfCreditsError(f"{label} out of balance (HTTP 402): top up and rerun the batch")
    if not resp.ok:
        raise LLMError(f"{label} HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        body = resp.json()
    except ValueError as exc:
        raise LLMError(f"{label} returned something that is not JSON") from exc
    params = {k: v for k, v in payload.items() if k not in ("messages", "state")}
    raw.append("llm", [body], endpoint=endpoint, input_sha256=key, params=params)
    _count(body.get("usage"), cached=False)
    result = parse(body)  # if it does not bring usable JSON, it is not cached (but the raw already stayed)
    if use_cache:
        _disk_cache().set(key, body)
    return result, body, False


def parse_json_object(content: str) -> dict[str, Any]:
    """Tolerant JSON: the full text or the first ``{...}`` it contains."""
    text = (content or "").strip()
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        start, end = text.find("{"), text.rfind("}")
        if start < 0 or end <= start:
            raise LLMError(f"the response does not bring JSON: {text[:150]!r}") from None
        try:
            data = json.loads(text[start : end + 1])
        except json.JSONDecodeError as exc:
            raise LLMError(f"invalid JSON in the response: {exc}") from None
    if not isinstance(data, dict):
        raise LLMError("the response JSON is not an object")
    return data


def _message_json(body: dict[str, Any]) -> dict[str, Any]:
    choices = body.get("choices") or [{}]
    return parse_json_object(((choices[0] or {}).get("message") or {}).get("content") or "")


def chat_json(
    system: str,
    user: str,
    *,
    model: str,
    temperature: float | None = None,
    max_tokens: int | None = None,
    extra: dict[str, Any] | None = None,
    timeout: float = 120,
    cache: bool = True,
) -> LLMResponse:
    """Chat with ``response_format=json_object``; returns the JSON of the response.

    ``extra`` adds NanoGPT-specific fields (for example prompt cache ones); OpenRouter does not get them.
    """
    payload: dict[str, Any] = {
        "model": model,
        "messages": [{"role": "system", "content": system}, {"role": "user", "content": user}],
        "response_format": {"type": "json_object"},
    }
    if temperature is not None:
        payload["temperature"] = temperature
    if max_tokens is not None:
        payload["max_tokens"] = max_tokens
    if provider() == "nanogpt":
        payload.update(extra or {})
    data, body, cached = _call("chat/completions", payload, _message_json, timeout=timeout, use_cache=cache)
    return LLMResponse(data=data, usage=body.get("usage") or {}, model=body.get("model") or model, cached=cached)


def systemone(
    state: dict[str, Any], questions: dict[str, Any], *, model: str, timeout: float = 90, cache: bool = True
) -> dict[str, Any]:
    """``/systemone`` (TypeSafe/Jev, NanoGPT only): typed responses, without generated text."""
    if not has_jev():
        raise LLMError("Jev (/systemone) is only on NanoGPT")
    payload = {"model": model, "state": state, "questions": questions}
    data, _, _ = _call("systemone", payload, lambda body: body, timeout=timeout, use_cache=cache)
    return data


# --- Normalization of what the LLM returns -------------------------------------------


def as_bool(value: Any) -> bool | None:
    if isinstance(value, bool):
        return value
    text = str(value).strip().lower()
    if text in {"true", "sí", "si", "yes", "1"}:
        return True
    if text in {"false", "no", "0"}:
        return False
    return None


def as_score(value: Any, low: int = 0, high: int = 100) -> int | None:
    try:
        return max(low, min(high, int(value))) if value is not None else None
    except (TypeError, ValueError):
        return None


def as_text(value: Any) -> str | None:
    text = str(value).strip() if value is not None else ""
    return text or None


def as_choice(value: Any, allowed: Iterable[str]) -> str | None:
    text = (as_text(value) or "").lower()
    return text if text in set(allowed) else None
