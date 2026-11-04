from __future__ import annotations

import os
import time

import pytest
import requests

from jobradar import http_client


def _resp(status: int, body: str = "", headers: dict | None = None) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = body.encode("utf-8")
    r.headers.update(headers or {})
    r.url = "https://api.example.com/x"
    return r


class _FakeSession:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def request(self, method, url, **kwargs):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def sleeps(monkeypatch):
    waited: list[float] = []
    monkeypatch.setattr(http_client.time, "sleep", waited.append)
    return waited


def _use(monkeypatch, outcomes) -> _FakeSession:
    fake = _FakeSession(outcomes)
    monkeypatch.setattr(http_client, "session", lambda: fake)
    return fake


def test_retries_5xx_and_returns_good_one(monkeypatch, sleeps):
    fake = _use(monkeypatch, [_resp(503), _resp(502), _resp(200, "ok")])
    resp = http_client.get("https://api.example.com/x", retries=3)
    assert resp.status_code == 200 and fake.calls == 3 and len(sleeps) == 2


def test_does_not_retry_authentication_errors(monkeypatch, sleeps):
    fake = _use(monkeypatch, [_resp(401), _resp(200)])
    assert http_client.get("https://api.example.com/x").status_code == 401
    assert fake.calls == 1 and sleeps == []


def test_respects_retry_after(monkeypatch, sleeps):
    _use(monkeypatch, [_resp(429, headers={"Retry-After": "7"}), _resp(200)])
    http_client.get("https://api.example.com/x")
    assert sleeps == [7.0]


def test_network_failure_retries_and_raises_at_end(monkeypatch, sleeps):
    fake = _use(monkeypatch, [requests.ConnectionError("x")] * 3)
    with pytest.raises(requests.ConnectionError):
        http_client.get("https://api.example.com/x", retries=2)
    assert fake.calls == 3


def test_retry_if_decides_by_body(monkeypatch, sleeps):
    fake = _use(monkeypatch, [_resp(503, "authentication failed"), _resp(200)])
    resp = http_client.get("https://api.example.com/x", retry_if=lambda r: "authentication" not in r.text)
    assert resp.status_code == 503 and fake.calls == 1


def test_pause_by_domain(monkeypatch, sleeps):
    clock = [100.0]
    monkeypatch.setattr(http_client.time, "monotonic", lambda: clock[0])
    http_client._next_slot.clear()
    http_client.throttle("https://a.example.com/1", 2.0)
    http_client.throttle("https://a.example.com/2", 2.0)
    http_client.throttle("https://b.example.com/1", 2.0)
    assert sleeps == [2.0]


def test_disk_cache_expires(tmp_path):
    cache = http_client.DiskCache(tmp_path, ttl=60)
    key = cache.key("https://x", {"q": 1})
    assert cache.get(key) is None
    cache.set(key, {"a": 1})
    assert cache.get(key) == {"a": 1}
    path = cache._path(key)
    old = time.time() - 120
    os.utime(path, (old, old))
    assert cache.get(key) is None


def test_cached_get_saves_only_200_responses(monkeypatch, sleeps, tmp_path):
    cache = http_client.DiskCache(tmp_path)
    fake = _use(monkeypatch, [_resp(404), _resp(200, "hello"), _resp(200, "other")])
    assert http_client.cached_get("https://x.example/p", cache=cache).status_code == 404
    first = http_client.cached_get("https://x.example/p", cache=cache)
    second = http_client.cached_get("https://x.example/p", cache=cache)
    assert first.text == "hello" and not first.from_cache
    assert second.text == "hello" and second.from_cache and fake.calls == 2
