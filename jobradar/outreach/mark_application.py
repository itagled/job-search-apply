#!/usr/bin/env python3
"""Registers the result of an application in ``job_post`` (status + metadata).

    jobradar mark-application --id 123 --status applied --via "ashby" --note "salary expectation sent"
    jobradar mark-application --external-id 4469129032 --status pending \\
        --reason "captcha" --url "https://..." --note "form filled in, captcha missing"
    jobradar mark-application --id 123 --status closed --note "no longer accepting applications"
    jobradar mark-application --id 123 --hiring-team '[{"name": "Ana", "title": "VP Sales", "linkedin_url": "..."}]'
    jobradar mark-application --id 123 --status applied --via getonbrd --submitted @enviado.json

status: applied → status='applied' ; pending → status unchanged + metadata.application_pending ;
closed/skipped → status='discarded' (+ reason). Affects all rows with that external_id.
"""

from __future__ import annotations

import argparse
import json
import logging
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import select
from sqlalchemy.orm import Session

from jobradar.db.models import JobPost
from jobradar.db.session import SessionLocal

log = logging.getLogger(__name__)


def record_outcome(
    session: Session,
    *,
    job_id: int | None = None,
    external_id: str | None = None,
    status: str | None = None,
    via: str = "",
    note: str = "",
    reason: str = "",
    url: str = "",
    hiring_team: list[dict[str, Any]] | None = None,
    submitted: dict[str, Any] | None = None,
) -> dict[str, Any] | None:
    """Save an application outcome on every ``job_post`` row of the same posting (no commit).

    ``applied`` sets status='applied'; ``pending`` keeps the status and records what is missing in
    ``application_pending``; ``closed``/``skipped`` set status='discarded' (never over an applied
    one). Returns a summary, or ``None`` if the posting doesn't exist.
    """
    now = datetime.now(UTC).isoformat()
    meta: dict[str, Any] = {}
    if status == "applied":
        meta.update({"applied_at": now, "applied_via": via})
        if note:
            meta["note"] = note
    elif status == "pending":
        meta["application_pending"] = {"at": now, "reason": reason, "url": url, "via": via, "note": note}
    elif status in ("closed", "skipped"):
        meta.update({"discarded_at": now, "discard_reason": reason or note or status})
    if hiring_team is not None:
        meta["hiring_team"] = hiring_team
    if submitted is not None:
        meta["application_submitted"] = {**submitted, "at": now}

    found = JobPost.id == job_id if job_id else JobPost.external_id == external_id
    ext = session.scalar(select(JobPost.external_id).where(found).limit(1))
    if ext is None:
        return None
    new_status = {"applied": "applied", "closed": "discarded", "skipped": "discarded"}.get(status or "")
    rows = 0
    for jp in session.scalars(select(JobPost).where(JobPost.external_id == ext)):
        if new_status == "discarded" and jp.status == "applied":
            continue  # closing a posting never undoes an application
        jp.source_metadata = {**(jp.source_metadata or {}), **meta}
        if new_status:
            jp.status = new_status
        rows += 1
    session.flush()
    return {"external_id": ext, "rows": rows, "status": status, "meta_keys": list(meta)}


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Register application result")
    g = ap.add_mutually_exclusive_group(required=True)
    g.add_argument("--id", type=int)
    g.add_argument("--external-id")
    ap.add_argument("--status", choices=["applied", "pending", "closed", "skipped"])
    ap.add_argument(
        "--via",
        default="",
        help='how you applied, e.g. "ashby" or "linkedin + linkedin message" (events backfill reads it)',
    )
    ap.add_argument("--note", default="")
    ap.add_argument("--reason", default="", help="reason for pending/closed/skipped")
    ap.add_argument("--url", default="", help="link to resume the pending application")
    ap.add_argument("--hiring-team", default="", help="JSON with the hiring team people visible in the job posting")
    ap.add_argument(
        "--submitted",
        default="",
        help="JSON (or @file.json) with what was submitted: salary expectation, CV, answers → metadata.application_submitted",
    )
    args = ap.parse_args(argv)

    submitted = None
    if args.submitted:
        raw = Path(args.submitted[1:]).read_text(encoding="utf-8") if args.submitted.startswith("@") else args.submitted
        submitted = json.loads(raw)
    with SessionLocal() as s:
        res = record_outcome(
            s,
            job_id=args.id,
            external_id=args.external_id,
            status=args.status,
            via=args.via,
            note=args.note,
            reason=args.reason,
            url=args.url,
            hiring_team=json.loads(args.hiring_team) if args.hiring_team else None,
            submitted=submitted,
        )
        if res is None:
            log.error("posting not found")
            return 1
        s.commit()
    print(json.dumps(res))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
