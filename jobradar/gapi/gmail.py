"""Gmail: read emails (alerts, replies) and draft or send (applications, outreach).

Uses two profiles from ``jobradar.gapi.auth``: ``gmail_read`` for job alerts
(also in CI) and ``gmail_send`` for drafts, sends, and reading replies.
"""

from __future__ import annotations

import base64
import html
import mimetypes
import re
from collections.abc import Iterable
from email.message import EmailMessage
from pathlib import Path
from typing import Any

from jobradar import config
from jobradar.gapi import auth

READ_PROFILE = "gmail_read"
SEND_PROFILE = "gmail_send"


def service(profile: str = SEND_PROFILE) -> Any:
    """Authenticated Gmail API client."""
    return auth.service("gmail", "v1", profile)


# --- reading -----------------------------------------------------------------------------


def list_message_ids(query: str, *, max_results: int = 100, svc: Any = None) -> list[str]:
    """Ids of messages matching a Gmail search (paginates up to ``max_results``)."""
    svc = svc or service(READ_PROFILE)
    ids: list[str] = []
    page_token: str | None = None
    while len(ids) < max_results:
        kwargs: dict[str, Any] = {"userId": "me", "q": query, "maxResults": min(500, max_results - len(ids))}
        if page_token:
            kwargs["pageToken"] = page_token
        res = auth.execute(svc.users().messages().list(**kwargs))
        ids += [str(m["id"]) for m in res.get("messages", []) if m.get("id")]
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return ids


def get_message(message_id: str, *, fmt: str = "full", svc: Any = None, **kwargs: Any) -> dict[str, Any]:
    """A message (``full``, ``metadata``, or ``minimal``)."""
    svc = svc or service(READ_PROFILE)
    return auth.execute(svc.users().messages().get(userId="me", id=message_id, format=fmt, **kwargs))


def _decode(data: str) -> str:
    return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4)).decode("utf-8", errors="replace")


def part_by_mime(payload: dict[str, Any], mime_type: str) -> str:
    """Text of the first part with that ``mimeType`` (e.g., ``text/html``), or ``""``."""
    data = (payload.get("body") or {}).get("data")
    if str(payload.get("mimeType", "")).lower() == mime_type.lower() and data:
        return _decode(data)
    for part in payload.get("parts") or []:
        found = part_by_mime(part, mime_type)
        if found:
            return found
    return ""


def first_body(payload: dict[str, Any]) -> str:
    """Text of the first part that carries content, whatever its type."""
    data = (payload.get("body") or {}).get("data")
    if data:
        return _decode(data)
    for part in payload.get("parts") or []:
        found = first_body(part)
        if found:
            return found
    return ""


def plain_text(payload: dict[str, Any]) -> str:
    """Readable message text: prefer text/plain; otherwise, the HTML without tags. No quotes."""
    plain: list[str] = []
    rich: list[str] = []

    def walk(part: dict[str, Any]) -> None:
        data = (part.get("body") or {}).get("data")
        if data:
            (rich if part.get("mimeType") == "text/html" else plain).append(_decode(data))
        for sub in part.get("parts") or []:
            walk(sub)

    walk(payload)
    text = "\n".join(plain) if plain else re.sub(r"<[^>]+>", " ", html.unescape("\n".join(rich)))
    text = re.sub(r"\n>.*", "", text)  # reply quotes
    text = re.sub(r"[^\S\n]+", " ", text)  # any whitespace (also &nbsp;) except line breaks
    return re.sub(r"\n\s*\n+", "\n", text).strip()


def header(message: dict[str, Any], name: str) -> str:
    for h in (message.get("payload") or {}).get("headers", []):
        if h.get("name", "").lower() == name.lower():
            return h.get("value", "")
    return ""


# --- sending ----------------------------------------------------------------------------


def _sender() -> str:
    """Sender email (``GMAIL_SENDER``)."""
    return config.require("GMAIL_SENDER")


def build_message(
    to: str,
    subject: str,
    body: str,
    *,
    attachments: Iterable[Path] = (),
    sender_name: str | None = None,
    cc: str | None = None,
) -> dict[str, str]:
    """MIME message (plain text + attachments) ready for the API (``{'raw': ...}``)."""
    msg = EmailMessage()
    msg["To"] = to
    name = sender_name or config.get("GMAIL_SENDER_NAME") or ""
    msg["From"] = f"{name} <{_sender()}>" if name else _sender()
    msg["Subject"] = subject
    if cc:
        msg["Cc"] = cc
    msg.set_content(body)
    for path in attachments:
        path = Path(path)
        ctype, _ = mimetypes.guess_type(path.name)
        maintype, subtype = (ctype or "application/octet-stream").split("/", 1)
        msg.add_attachment(path.read_bytes(), maintype=maintype, subtype=subtype, filename=path.name)
    return {"raw": base64.urlsafe_b64encode(msg.as_bytes()).decode()}


def send(to: str, subject: str, body: str, *, attachments: Iterable[Path] = (), svc: Any = None) -> dict:
    """Send the email. Returns ``{'id', 'threadId', ...}``."""
    svc = svc or service()
    message = build_message(to, subject, body, attachments=attachments)
    return auth.execute(svc.users().messages().send(userId="me", body=message), idempotent=False)


def create_draft(to: str, subject: str, body: str, *, attachments: Iterable[Path] = (), svc: Any = None) -> dict:
    """Save the email as a draft. Returns ``{'id', 'message': {...}}``."""
    svc = svc or service()
    message = build_message(to, subject, body, attachments=attachments)
    return auth.execute(svc.users().drafts().create(userId="me", body={"message": message}), idempotent=False)


def send_draft(draft_id: str, *, svc: Any = None) -> dict:
    """Send an existing draft."""
    svc = svc or service()
    return auth.execute(svc.users().drafts().send(userId="me", body={"id": draft_id}), idempotent=False)


def profile(svc: Any = None) -> dict:
    """Authenticated account (emailAddress, messagesTotal...)."""
    svc = svc or service()
    return auth.execute(svc.users().getProfile(userId="me"))
