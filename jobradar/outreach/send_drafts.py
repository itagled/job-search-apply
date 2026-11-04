#!/usr/bin/env python3
"""Sends the Gmail drafts marked for scheduling (``outreach_status='to_schedule'``).

Intended to run once at the scheduled time (Windows task). Does not send
before ``--not-before`` (local time). If the draft no longer exists (because it
was scheduled or sent from Gmail), it marks it ``scheduled_in_gmail`` and does not resend.

    jobradar send-drafts --not-before 2026-09-25T09:00
"""

from __future__ import annotations

import argparse
import json
import logging
import random
import time
from datetime import UTC, datetime

from sqlalchemy import select

from jobradar.db.models import ApplicationContact as C
from jobradar.db.session import SessionLocal
from jobradar.gapi import gmail
from jobradar.outreach.send_outreach import update_contact

log = logging.getLogger(__name__)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Send scheduled drafts")
    ap.add_argument("--not-before", default="", help="minimum local time, ISO (e.g. 2026-09-25T09:00)")
    ap.add_argument("--gap-min", type=float, default=300.0, help="minimum seconds between sends (random)")
    ap.add_argument("--gap-max", type=float, default=600.0, help="maximum seconds between sends (random)")
    args = ap.parse_args(argv)

    if args.not_before and datetime.now() < datetime.fromisoformat(args.not_before):
        log.info("not yet %s: nothing sent", args.not_before)
        return 0

    s = SessionLocal()
    rows = (
        s.execute(
            select(C.id, C.company, C.full_name, C.email, C.gmail_draft_id)
            .where(C.outreach_status == "to_schedule", C.gmail_draft_id.is_not(None))
            .order_by(C.id)
        )
        .mappings()
        .all()
    )
    svc = gmail.service()
    sent = gone = errors = 0
    for n, r in enumerate(rows):
        try:
            res = gmail.send_draft(r["gmail_draft_id"], svc=svc)
            update_contact(
                s, r["id"], outreach_status="sent", gmail_message_id=res.get("id"), sent_at=datetime.now(UTC)
            )
            sent += 1
            log.info("sent [%s] %s <%s> (%s)", r["id"], r["full_name"], r["email"], r["company"])
        except Exception as exc:  # noqa: BLE001
            msg = str(exc)
            if "404" in msg or "notFound" in msg or "not found" in msg.lower():
                update_contact(s, r["id"], outreach_status="scheduled_in_gmail")
                gone += 1
                log.warning("[%s] the draft no longer exists (scheduled or sent from Gmail)", r["id"])
            else:
                errors += 1
                log.error("[%s] %s: %s", r["id"], r["email"], msg[:200])
        s.commit()
        if n < len(rows) - 1:
            wait = random.uniform(args.gap_min, args.gap_max)
            log.info("next send in %.1f min", wait / 60)
            time.sleep(wait)
    s.close()
    print(json.dumps({"sent": sent, "already_scheduled": gone, "errors": errors, "at": datetime.now().isoformat()}))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
