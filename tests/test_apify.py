from __future__ import annotations

import json

import pytest
import requests

from jobradar import apify, raw


def _resp(status: int, payload) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(payload).encode("utf-8")
    return r


class _FakeApify:
    """Simulates the API: a run that goes through RUNNING and a dataset in pages."""

    def __init__(self, statuses, items, cost=0.42):
        self.statuses = list(statuses)
        self.items = items
        self.cost = cost
        self.calls: list[tuple[str, str, dict]] = []

    def _run(self, status):
        return {
            "data": {
                "id": "r1",
                "status": status,
                "defaultDatasetId": "d1",
                "usageTotalUsd": self.cost,
                "chargedEventCounts": {"short-profile": 3},
            }
        }

    def post(self, url, **kw):
        self.calls.append(("POST", url, kw))
        if url.endswith("/abort"):
            return _resp(200, self._run("ABORTED"))
        return _resp(201, self._run("READY"))

    def get(self, url, **kw):
        self.calls.append(("GET", url, kw))
        if "/datasets/" in url:
            offset, limit = kw["params"]["offset"], kw["params"]["limit"]
            return _resp(200, self.items[offset : offset + limit])
        status = self.statuses.pop(0) if len(self.statuses) > 1 else self.statuses[0]
        return _resp(200, self._run(status))


@pytest.fixture
def fake(monkeypatch, tmp_path):
    monkeypatch.setenv("APIFY_API_TOKEN", "dummy")
    monkeypatch.setattr(raw, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(raw, "THIRD_PARTY_DIR", tmp_path / "raw" / "third_party")
    monkeypatch.setattr(apify.time, "sleep", lambda s: None)
    monkeypatch.setattr(apify, "PAGE_SIZE", 2)

    def install(statuses, items):
        api = _FakeApify(statuses, items)
        monkeypatch.setattr(apify.http_client, "post", api.post)
        monkeypatch.setattr(apify.http_client, "get", api.get)
        return api

    return install


def test_run_waits_for_page_and_reads_real_cost(fake, tmp_path):
    api = fake(["RUNNING", "SUCCEEDED"], [{"id": i} for i in range(5)])
    res = apify.run("user/actor", {"q": 1}, raw_kind="sample")
    assert [it["id"] for it in res.items] == [0, 1, 2, 3, 4]
    assert (res.status, res.cost_usd, res.charged_events["short-profile"]) == ("SUCCEEDED", 0.42, 3)
    start = api.calls[0]
    assert start[1].endswith("/acts/user~actor/runs") and start[2]["retries"] == 0
    line = json.loads(next((tmp_path / "raw" / "sample").glob("*.jsonl")).read_text(encoding="utf-8"))
    assert line["n_items"] == 5 and line["cost_usd"] == 0.42 and len(line["data"]) == 5


def test_local_deadline_aborts_and_keeps_partial(fake, monkeypatch):
    clock = iter([0.0, 10_000.0, 10_000.0])
    monkeypatch.setattr(apify.time, "monotonic", lambda: next(clock))
    api = fake(["RUNNING"], [{"id": 1}])
    res = apify.run("a/b", {}, timeout_secs=60, accept_partial=True)
    assert res.status == "ABORTED" and res.items == [{"id": 1}]
    assert any(url.endswith("/actor-runs/r1/abort") for _, url, _ in api.calls)


def test_failed_run_is_an_error_unless_partial_is_accepted(fake):
    fake(["FAILED"], [])
    with pytest.raises(apify.ApifyError, match="FAILED"):
        apify.run("a/b", {})


def test_token_rejected(monkeypatch):
    monkeypatch.setenv("APIFY_API_TOKEN", "malo")
    monkeypatch.setattr(apify.http_client, "post", lambda url, **kw: _resp(401, {"error": "x"}))
    with pytest.raises(apify.ApifyAuthError):
        apify.run_sync("a/b", {})


def test_without_token():
    with pytest.raises(apify.ApifyAuthError, match="APIFY_API_TOKEN"):
        apify.run_sync("a/b", {})


def test_minimize_removes_photos_and_lists_of_other_people():
    item = {
        "title": "GTM",
        "author": {"name": "Ana", "avatar": "x.jpg", "profilePicture": {"url": "y"}},
        "comments": [{"author": "otra persona"}],
        "likesCount": 4,
        "postImages": ["z.png"],
    }
    assert apify.minimize(item) == {"title": "GTM", "author": {"name": "Ana"}, "likesCount": 4}


def test_run_sync_with_third_party_saves_minimized(monkeypatch, tmp_path):
    monkeypatch.setenv("APIFY_API_TOKEN", "dummy")
    monkeypatch.setattr(raw, "THIRD_PARTY_DIR", tmp_path / "third_party")
    monkeypatch.setattr(
        apify.http_client, "post", lambda url, **kw: _resp(201, [{"text": "hello", "authorPhoto": "a.jpg"}, "basura"])
    )
    items = apify.run_sync("a/b", {"targetUrls": ["u"]}, raw_kind="posts", third_party=True)
    assert items == [{"text": "hello", "authorPhoto": "a.jpg"}]
    saved = json.loads(next((tmp_path / "third_party" / "posts").glob("*.jsonl")).read_text(encoding="utf-8"))
    assert saved["data"] == [{"text": "hello"}]


def test_contacts_is_soft_fail(monkeypatch):
    from jobradar.companies import contacts_apify

    monkeypatch.setenv("APIFY_API_TOKEN", "dummy")

    def fails(*a, **k):
        raise apify.ApifyError("run FAILED")

    monkeypatch.setattr(contacts_apify.apify, "run", fails)
    assert contacts_apify._run_actor("https://linkedin.com/company/x", ["25"], max_items=5, timeout=60) == ([], 0)
