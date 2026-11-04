"""Google Drive: export Docs (docx, PDF, text), create folders and upload Word as Google Docs.

Profile ``google``. Uses the Drive API and not the Docs API (which is turned off in the Google
Cloud project): Docs are edited as .docx and re-uploaded converted.
"""

from __future__ import annotations

import io
from pathlib import Path
from typing import Any

from jobradar.gapi import auth

DOCX_MIME = "application/vnd.openxmlformats-officedocument.wordprocessingml.document"
GDOC_MIME = "application/vnd.google-apps.document"
FOLDER_MIME = "application/vnd.google-apps.folder"


def service() -> Any:
    return auth.service("drive", "v3", "google")


def export(file_id: str, mime_type: str, *, svc: Any = None) -> bytes:
    svc = svc or service()
    return auth.execute(svc.files().export(fileId=file_id, mimeType=mime_type))


def export_docx(file_id: str, *, svc: Any = None) -> bytes:
    return export(file_id, DOCX_MIME, svc=svc)


def export_text(file_id: str, *, svc: Any = None) -> str:
    return export(file_id, "text/plain", svc=svc).decode("utf-8")


def export_pdf(file_id: str, out_path: Path, *, svc: Any = None) -> Path:
    out_path.parent.mkdir(parents=True, exist_ok=True)
    out_path.write_bytes(export(file_id, "application/pdf", svc=svc))
    return out_path


def _find(name: str, mime_type: str, parent: str | None, svc: Any) -> str | None:
    query = f"name = '{name}' and mimeType = '{mime_type}' and trashed = false"
    if parent:
        query += f" and '{parent}' in parents"
    found = auth.execute(svc.files().list(q=query, fields="files(id)")).get("files", [])
    return found[0]["id"] if found else None


def ensure_folder(name: str, parent: str | None = None, *, svc: Any = None) -> str:
    """Id of the folder ``name`` (inside ``parent``); creates it if it does not exist."""
    svc = svc or service()
    existing = _find(name, FOLDER_MIME, parent, svc)
    if existing:
        return existing
    meta = {"name": name, "mimeType": FOLDER_MIME, **({"parents": [parent]} if parent else {})}
    return auth.execute(svc.files().create(body=meta, fields="id"), idempotent=False)["id"]


def upload_as_gdoc(data: bytes, name: str, parent: str | None, *, svc: Any = None) -> str:
    """Upload a .docx converted to Google Docs; if there is already a Doc with that name, replace it."""
    from googleapiclient.http import MediaIoBaseUpload

    svc = svc or service()
    media = MediaIoBaseUpload(io.BytesIO(data), mimetype=DOCX_MIME, resumable=False)
    existing = _find(name, GDOC_MIME, parent, svc)
    if existing:
        return auth.execute(svc.files().update(fileId=existing, media_body=media, fields="id"), idempotent=False)["id"]
    meta = {"name": name, "mimeType": GDOC_MIME, **({"parents": [parent]} if parent else {})}
    return auth.execute(svc.files().create(body=meta, media_body=media, fields="id"), idempotent=False)["id"]
