from __future__ import annotations

import base64
import email

import httplib2
import pytest
from googleapiclient.errors import HttpError

from jobradar import config
from jobradar.gapi import auth, gmail


def _b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode("utf-8")).decode().rstrip("=")


def _http_error(status: int, reason: str) -> HttpError:
    return HttpError(httplib2.Response({"status": status}), reason.encode("utf-8"))


class _Request:
    def __init__(self, outcomes):
        self.outcomes = list(outcomes)
        self.calls = 0

    def execute(self):
        self.calls += 1
        outcome = self.outcomes.pop(0)
        if isinstance(outcome, Exception):
            raise outcome
        return outcome


@pytest.fixture
def no_sleep(monkeypatch):
    monkeypatch.setattr(auth.time, "sleep", lambda s: None)


def test_execute_retries_quota_and_5xx_on_reads(no_sleep):
    req = _Request([_http_error(429, "rateLimitExceeded"), _http_error(503, "backend"), {"ok": 1}])
    assert auth.execute(req) == {"ok": 1} and req.calls == 3


def test_execute_does_not_retry_5xx_on_sends(no_sleep):
    req = _Request([_http_error(500, "backend error"), {"id": "x"}])
    with pytest.raises(HttpError):
        auth.execute(req, idempotent=False)
    assert req.calls == 1
    quota = _Request([_http_error(429, "userRateLimitExceeded"), {"id": "x"}])
    assert auth.execute(quota, idempotent=False) == {"id": "x"}


def test_execute_does_not_retry_permission_errors(no_sleep):
    req = _Request([_http_error(403, "insufficientPermissions"), {}])
    with pytest.raises(HttpError):
        auth.execute(req)
    assert req.calls == 1


def test_read_without_variables_asks_reauthorize():
    with pytest.raises(auth.MissingCredentialsError, match="GMAIL_REFRESH_TOKEN"):
        auth.credentials("gmail_read")


def test_profile_with_missing_file(monkeypatch, tmp_path):
    monkeypatch.setattr(config, "SECRETS_DIR", tmp_path)
    with pytest.raises(auth.MissingCredentialsError, match="reauth google"):
        auth.credentials("google")
    assert auth.scopes("google") == []


def test_build_message_creates_sender_from_env(monkeypatch):
    monkeypatch.setenv("GMAIL_SENDER", "yo@example.com")
    monkeypatch.setenv("GMAIL_SENDER_NAME", "Nombre Apellido")
    msg = email.message_from_bytes(
        base64.urlsafe_b64decode(gmail.build_message("a@example.com", "Hola", "cuerpo")["raw"])
    )
    assert msg["From"] == "Nombre Apellido <yo@example.com>" and msg["To"] == "a@example.com"


def test_build_message_without_sender_fails_clearly():
    with pytest.raises(config.ConfigError, match="GMAIL_SENDER"):
        gmail.build_message("a@example.com", "x", "y")


def test_decode_message_parts():
    payload = {
        "mimeType": "multipart/alternative",
        "parts": [
            {"mimeType": "text/plain", "body": {"data": _b64("Hola Ana\n> cita vieja\nchao")}},
            {"mimeType": "text/html", "body": {"data": _b64("<p>Hola <b>Ana</b></p>")}},
        ],
        "headers": [],
    }
    assert gmail.part_by_mime(payload, "text/html") == "<p>Hola <b>Ana</b></p>"
    assert gmail.first_body(payload) == "Hola Ana\n> cita vieja\nchao"
    assert gmail.plain_text(payload) == "Hola Ana\nchao"
    html_only = {"mimeType": "text/html", "body": {"data": _b64("<p>Hola&nbsp;<b>Ana</b></p>")}}
    assert gmail.plain_text(html_only) == "Hola Ana"
    assert gmail.header({"payload": {"headers": [{"name": "Subject", "value": "Oferta"}]}}, "subject") == "Oferta"


def test_list_message_ids_paginates(no_sleep):
    pages = [{"messages": [{"id": "1"}, {"id": "2"}], "nextPageToken": "t"}, {"messages": [{"id": "3"}]}]
    seen_kwargs = []

    class _Messages:
        def list(self, **kwargs):
            seen_kwargs.append(kwargs)
            return _Request([pages.pop(0)])

    class _Users:
        def messages(self):
            return _Messages()

    class _Svc:
        def users(self):
            return _Users()

    assert gmail.list_message_ids("from:jobs", max_results=10, svc=_Svc()) == ["1", "2", "3"]
    assert "pageToken" not in seen_kwargs[0] and seen_kwargs[1]["pageToken"] == "t"
