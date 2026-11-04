#!/usr/bin/env python3
"""Sends (or leaves as draft) emails to decision makers from personal Gmail.

Takes ``application_contact`` with ``outreach_status`` in:
- ``ready``        → sends and leaves as ``sent`` (+ ``gmail_message_id``, ``sent_at``)
- ``ready_draft``  → creates draft and leaves as ``draft`` (+ ``gmail_draft_id``)

Each row must include ``email``, ``email_subject``, ``email_body`` and ``cv_file``
(path to the attached PDF). Pauses between sends to avoid looking like spam and never
sends twice to the same email (if there is already a ``sent`` row with that email, skips).

    jobradar send-outreach --dry-run
    jobradar send-outreach --limit 20 [--min-gap 25] [--ids 3,4,5]
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import time
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.orm import Session

from jobradar import config
from jobradar.db.models import ApplicationContact as C
from jobradar.db.session import SessionLocal
from jobradar.gapi import gmail

log = logging.getLogger(__name__)


def pending(s: Session, ids: list[int], limit: int) -> list[Any]:
    """Contacts with their email ready to send or to leave as draft (only ``ids`` if given)."""
    stmt = select(C.id, C.company, C.full_name, C.email, C.email_subject, C.email_body, C.cv_file, C.outreach_status)
    stmt = stmt.where(
        C.outreach_status.in_(("ready", "ready_draft")),
        C.email.is_not(None),
        C.email_subject.is_not(None),
        C.email_body.is_not(None),
    )
    if ids:
        stmt = stmt.where(C.id.in_(ids))
    return list(s.execute(stmt.order_by(C.outreach_status, C.id).limit(limit)).mappings())


def update_contact(s: Session, contact_id: int, *, note: str = "", **values: Any) -> None:
    """Update an ``application_contact`` row (no commit); ``note`` is appended to ``notes``."""
    if note:
        values["notes"] = func.coalesce(C.notes, "") + note
    s.execute(update(C).where(C.id == contact_id).values(**values))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Send/draft outreach emails")
    ap.add_argument("--limit", type=int, default=50)
    ap.add_argument("--gap-min", type=float, default=300.0, help="Minimum seconds between sends (random)")
    ap.add_argument("--gap-max", type=float, default=600.0, help="Maximum seconds between sends (random)")
    ap.add_argument("--ids", default="", help="application_contact ids separated by commas")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--draft-all",
        action="store_true",
        help="Sends nothing: 'ready' stays as draft in 'to_schedule' state (to schedule sending in Gmail)",
    )
    args = ap.parse_args(argv)

    s = SessionLocal()
    rows = pending(s, [int(x) for x in args.ids.split(",") if x.strip()], args.limit)
    log.info("pending: %d", len(rows))
    svc = None if args.dry_run else gmail.service()
    sent = drafts = skipped = errors = 0
    for n, r in enumerate(rows):
        cv = Path(r["cv_file"]) if r["cv_file"] else None
        if cv is not None and not cv.is_absolute():
            cv = config.ROOT / cv
        if cv is None or not cv.is_file():
            log.warning("[%s] %s: the CV attachment is missing (%s) -> error", r["id"], r["company"], r["cv_file"])
            update_contact(s, r["id"], outreach_status="error", note=" | CV missing")
            s.commit()
            errors += 1
            continue
        dup = s.scalar(
            select(C.id)
            .where(func.lower(C.email) == func.lower(r["email"]), C.outreach_status == "sent", C.id != r["id"])
            .limit(1)
        )
        if dup:
            log.info("[%s] %s: already contacted (row %s) -> skipped", r["id"], r["email"], dup)
            update_contact(s, r["id"], outreach_status="skipped", note=" | duplicate")
            s.commit()
            skipped += 1
            continue
        if args.dry_run:
            log.info(
                "[dry] %s -> %s <%s> (%s): %s",
                r["outreach_status"],
                r["full_name"],
                r["email"],
                r["company"],
                r["email_subject"],
            )
            continue
        try:
            if r["outreach_status"] == "ready" and args.draft_all:
                res = gmail.create_draft(r["email"], r["email_subject"], r["email_body"], attachments=[cv], svc=svc)
                update_contact(
                    s,
                    r["id"],
                    outreach_status="to_schedule",
                    gmail_draft_id=res.get("id"),
                    gmail_message_id=(res.get("message") or {}).get("id"),
                )
                drafts += 1
                log.info("draft to schedule [%s] %s <%s> (%s)", r["id"], r["full_name"], r["email"], r["company"])
            elif r["outreach_status"] == "ready":
                res = gmail.send(r["email"], r["email_subject"], r["email_body"], attachments=[cv], svc=svc)
                update_contact(
                    s, r["id"], outreach_status="sent", gmail_message_id=res.get("id"), sent_at=datetime.now(UTC)
                )
                sent += 1
                log.info("sent [%s] %s <%s> (%s)", r["id"], r["full_name"], r["email"], r["company"])
            else:
                res = gmail.create_draft(r["email"], r["email_subject"], r["email_body"], attachments=[cv], svc=svc)
                update_contact(s, r["id"], outreach_status="draft", gmail_draft_id=res.get("id"))
                drafts += 1
                log.info("draft [%s] %s <%s> (%s)", r["id"], r["full_name"], r["email"], r["company"])
            s.commit()
        except Exception as exc:  # noqa: BLE001 - logs and continues with the rest
            s.rollback()
            update_contact(s, r["id"], outreach_status="error", note=f" | {type(exc).__name__}: {str(exc)[:200]}")
            s.commit()
            errors += 1
            log.error("[%s] %s: %s", r["id"], r["email"], exc)
        if r["outreach_status"] == "ready" and not args.draft_all and n < len(rows) - 1:
            wait = random.uniform(args.gap_min, args.gap_max)
            log.info("next send in %.1f min", wait / 60)
            time.sleep(wait)
    s.close()
    print(
        json.dumps(
            {"sent": sent, "drafts": drafts, "skipped": skipped, "errors": errors, "at": datetime.now(UTC).isoformat()}
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
