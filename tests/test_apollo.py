from __future__ import annotations

import json

import pytest
import requests

from jobradar import apollo, raw


def _resp(status: int, payload) -> requests.Response:
    r = requests.Response()
    r.status_code = status
    r._content = json.dumps(payload).encode("utf-8")
    return r


@pytest.fixture
def api(monkeypatch, tmp_path):
    monkeypatch.setenv("APOLLO_API_KEY", "dummy")
    monkeypatch.setattr(raw, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(raw, "THIRD_PARTY_DIR", tmp_path / "raw" / "third_party")
    calls: list[dict] = []
    replies: list[requests.Response] = []

    def fake(method, url, **kw):
        calls.append({"method": method, "url": url, **kw})
        return replies.pop(0)

    monkeypatch.setattr(apollo.http_client, "request", fake)
    return calls, replies, tmp_path


def test_reveal_never_asks_phones_or_personal_emails(api):
    calls, replies, _ = api
    replies.append(_resp(200, {"matches": [{"id": "p1", "email": "ana@acme.example", "phone": "+56..."}]}))
    out = apollo.reveal([{"id": "p1"}])
    body = calls[0]["json"]
    assert all(body[flag] is False for flag in apollo.FORBIDDEN_FLAGS)
    assert out[0]["email"] == "ana@acme.example"


def test_raw_people_data_is_reduced_as_third_party(api):
    _, replies, tmp = api
    replies.append(
        _resp(
            200,
            {
                "people": [{"id": "p1", "name": "Ana", "photo_url": "x.jpg", "organization": {"name": "Acme"}}],
                "pagination": {"total": 1},
            },
        )
    )
    apollo.search_people("acme.example")
    saved = json.loads(next((tmp / "raw" / "third_party" / "apollo").glob("*.jsonl")).read_text(encoding="utf-8"))
    person = saved["data"]["people"][0]
    assert person["name"] == "Ana" and person["organization"] == "Acme" and "photo_url" not in person


def test_raw_company_data_is_saved_in_full(api):
    _, replies, tmp = api
    replies.append(_resp(200, {"organization": {"name": "Acme", "industry": "software"}}))
    assert apollo.enrich_organization("acme.example")["industry"] == "software"
    assert next((tmp / "raw" / "apollo").glob("*.jsonl")).exists()


def test_http_error_is_apollo_error(api):
    _, replies, _ = api
    replies.append(_resp(422, {"error": "bad"}))
    with pytest.raises(apollo.ApolloError, match="422"):
        apollo.search_organizations({"page": 1})


def test_only_retries_429(api):
    calls, replies, _ = api
    replies.append(_resp(200, {"organizations": []}))
    apollo.search_organizations({"page": 1})
    assert calls[0]["retry_status"] == frozenset({429})
