from __future__ import annotations

import json

import pytest
import requests

from jobradar import config, http_client, llm, raw


def _resp(status: int, payload) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = (payload if isinstance(payload, str) else json.dumps(payload)).encode("utf-8")
    return r


def _chat(content: str, usage: dict | None = None) -> dict:
    return {"model": "m", "choices": [{"message": {"content": content}}], "usage": usage or {"prompt_tokens": 10}}


@pytest.fixture
def nanogpt(monkeypatch, tmp_path):
    """Simulates NanoGPT: returns responses in order and logs each POST."""
    monkeypatch.setenv("NANOGPT_API_KEY", "dummy")
    monkeypatch.setattr(llm, "_cache", http_client.DiskCache(tmp_path / "cache"))
    monkeypatch.setattr(raw, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(llm, "_usage", {})
    calls: list[dict] = []
    queue: list[requests.Response] = []

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return queue.pop(0)

    monkeypatch.setattr(llm.http_client, "post", fake_post)
    return calls, queue, tmp_path


def test_chat_json_parses_and_caches(nanogpt):
    calls, queue, tmp = nanogpt
    queue.append(_resp(200, _chat('Claro: {"ok": true, "n": 3} listo')))
    first = llm.chat_json("sys", "user", model="m", temperature=0)
    second = llm.chat_json("sys", "user", model="m", temperature=0)
    assert first.data == {"ok": True, "n": 3} and not first.cached
    assert second.data == first.data and second.cached
    assert len(calls) == 1 and calls[0]["url"].endswith("/chat/completions")
    lines = (tmp / "raw" / "llm").glob("*.jsonl")
    record = json.loads(next(lines).read_text(encoding="utf-8").splitlines()[0])
    assert record["params"]["temperature"] == 0 and "messages" not in record["params"]
    assert record["input_sha256"] and record["data"]["usage"]["prompt_tokens"] == 10
    assert llm.usage_total() == {
        "calls": 2,
        "local_cache_hits": 1,
        "prompt_tokens": 10,
        "completion_tokens": 0,
        "provider_cached_tokens": 0,
    }


def test_jev_usage_counts_like_the_rest(nanogpt):
    _, queue, _ = nanogpt
    usage = {"input_tokens": 400, "output_tokens": 20, "estimated_cost": 0.0000168}
    queue.append(_resp(200, {"answers": {}, "usage": usage}))
    llm.systemone({"a": 1}, {}, model="m")
    assert llm.usage_total() == {
        "calls": 1,
        "prompt_tokens": 400,
        "completion_tokens": 20,
        "provider_cached_tokens": 0,
        "cost_usd": 0.000017,
    }


def test_out_of_credits_stops(nanogpt):
    _, queue, _ = nanogpt
    queue.append(_resp(402, {"error": "insufficient balance"}))
    with pytest.raises(llm.OutOfCreditsError):
        llm.chat_json("s", "u", model="m")


def test_response_without_json_not_cached(nanogpt):
    calls, queue, tmp = nanogpt
    queue += [_resp(200, _chat("no idea")), _resp(200, _chat('{"a": 1}'))]
    with pytest.raises(llm.LLMError):
        llm.chat_json("s", "u", model="m")
    assert llm.chat_json("s", "u", model="m").data == {"a": 1}
    assert len(calls) == 2
    assert len(next((tmp / "raw" / "llm").glob("*.jsonl")).read_text(encoding="utf-8").splitlines()) == 2


def test_the_provider_is_the_one_with_a_key(monkeypatch):
    assert (llm.provider(), llm.has_key()) == ("nanogpt", False)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    assert (llm.provider(), llm.has_key(), llm.has_jev()) == ("openrouter", True, False)
    monkeypatch.setenv("NANOGPT_API_KEY", "nano-key")
    assert (llm.provider(), llm.has_jev()) == ("nanogpt", True)
    monkeypatch.setenv("LLM_PROVIDER", "OpenRouter")
    assert llm.provider() == "openrouter"
    monkeypatch.setenv("LLM_PROVIDER", "openai")
    with pytest.raises(config.ConfigError, match="nanogpt, openrouter"):
        llm.provider()


def test_a_missing_key_says_where_to_get_one():
    with pytest.raises(config.ConfigError) as exc:
        llm.require_key()
    assert "https://nano-gpt.com/api" in str(exc.value) and "https://openrouter.ai/settings/keys" in str(exc.value)


def test_openrouter_gets_its_url_and_key_and_no_nanogpt_fields(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    calls: list[dict] = []
    queue = [
        _resp(200, _chat('{"ok": 1}', {"prompt_tokens": 50, "completion_tokens": 5, "cost": 0.00002})),
        _resp(402, {"error": {"code": 402, "message": "Insufficient credits"}}),
    ]

    def fake_post(url, **kwargs):
        calls.append({"url": url, **kwargs})
        return queue.pop(0)

    monkeypatch.setattr(llm.http_client, "post", fake_post)
    assert llm.chat_json("s", "u", model="m", extra=llm.PROMPT_CACHING_1H, cache=False).data == {"ok": 1}
    [call] = calls
    assert call["url"] == "https://openrouter.ai/api/v1/chat/completions"
    assert call["headers"]["Authorization"] == "Bearer or-key" and "promptCaching" not in call["json"]
    assert llm.usage_total()["cost_usd"] == 0.00002
    with pytest.raises(llm.OutOfCreditsError, match="OpenRouter"):
        llm.chat_json("s", "again", model="m", cache=False)
    with pytest.raises(llm.LLMError, match="only on NanoGPT"):
        llm.systemone({}, {}, model="typesafe/jev-latest")


def test_does_not_retry_if_error_is_credentials():
    assert llm._should_retry(_resp(503, "upstream timeout"))
    assert not llm._should_retry(_resp(503, "Authentication failed"))
    assert not llm._should_retry(_resp(401, "bad key"))


def test_normalization():
    assert llm.as_bool("Sí") is True and llm.as_bool("no") is False and llm.as_bool(None) is None
    assert llm.as_score("140") == 100 and llm.as_score("x") is None and llm.as_score(-5) == 0
    assert llm.as_text("  hello ") == "hello" and llm.as_text("") is None
    assert llm.as_choice("HIGH", ("high", "low")) == "high" and llm.as_choice("mid", ("high",)) is None


def test_company_classification_is_soft_fail(monkeypatch):
    from jobradar.companies import classify_llm

    def boom(*a, **k):
        raise llm.LLMError("HTTP 500")

    monkeypatch.setattr(classify_llm.llm, "chat_json", boom)
    assert classify_llm.classify_company_llm(name="Acme").error == "HTTP 500"

    def out_of_credits(*a, **k):
        raise llm.OutOfCreditsError("402")

    monkeypatch.setattr(classify_llm.llm, "chat_json", out_of_credits)
    with pytest.raises(llm.OutOfCreditsError):
        classify_llm.classify_company_llm(name="Acme")


def test_classification_normalizes_output(monkeypatch):
    from jobradar.companies import classify_llm, classify_post_llm

    monkeypatch.setattr(
        classify_llm.llm,
        "chat_json",
        lambda *a, **k: llm.LLMResponse(
            {"is_startup": "true", "fit_tier": "HIGH", "fit_score": 120, "company_type": " Startup "}
        ),
    )
    res = classify_llm.classify_company_llm(name="Acme")
    assert (res.is_startup, res.fit_tier, res.fit_score, res.company_type) == (True, "high", 100, "startup")

    monkeypatch.setattr(
        classify_post_llm.llm,
        "chat_json",
        lambda *a, **k: llm.LLMResponse(
            {"is_job_post": True, "role_bucket": "sales", "detected_role": "SDR", "confidence": 80}
        ),
    )
    post = classify_post_llm.classify_post_llm("Buscamos SDR")
    assert (post.is_job_post, post.role_bucket, post.detected_role) == (True, "other", "SDR")
    assert classify_post_llm.classify_post_llm("  ").error == "empty_text"


def test_company_and_post_prompts_come_from_the_profile():
    from jobradar.companies import classify_llm, classify_post_llm

    company = classify_llm.system_prompt()
    assert (
        "companies in Chile for someone looking for GTM Engineer roles" in company and "A consumer fintech" in company
    )
    post = classify_post_llm.system_prompt()
    assert (
        '"gtm_engineer":' in post and "one of: gtm_engineer, growth, sdr_bdr, sales_automation, revops, other" in post
    )
