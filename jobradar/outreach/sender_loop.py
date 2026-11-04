#!/usr/bin/env python3
"""Continuous sender of emails to decision makers, with a global pace of 5-10 min between sends.

On each pass:
1. Creates drafts in Gmail for the ``ready_draft`` rows (does not send anything).
2. Sends one email: the oldest draft already in Gmail (``to_schedule``, sent as it is) or else
   the oldest ``ready`` row, only if between 5 and 10 min (random) have passed since the LAST
   recorded send (``max(sent_at)``), and only during business hours (08:30-19:30 local time).
   This way the pace is respected even if there are other sends.

Stops if ``data/outreach/STOP_SENDER`` exists.

    jobradar scheduled-send
"""

from __future__ import annotations

import argparse
import logging
import random
import time
from collections.abc import Mapping
from datetime import UTC, datetime
from datetime import time as dtime
from typing import Any

from sqlalchemy import func, select

from jobradar import config
from jobradar.db.models import ApplicationContact as C
from jobradar.db.session import SessionLocal
from jobradar.outreach import send_outreach

log = logging.getLogger(__name__)

STOP = config.DATA_DIR / "outreach" / "STOP_SENDER"
OPEN, CLOSE = dtime(8, 30), dtime(19, 30)


def _send_existing_draft(row: Mapping[str, Any]) -> None:
    """Sends an existing draft in Gmail (drafts.send) and marks it as sent."""
    from jobradar.gapi import gmail

    s = SessionLocal()
    try:
        res = gmail.send_draft(row["gmail_draft_id"])
        send_outreach.update_contact(
            s, row["id"], outreach_status="sent", gmail_message_id=res.get("id"), sent_at=datetime.now(UTC)
        )
        log.info("sent (draft) [%s] %s <%s> (%s)", row["id"], row["full_name"], row["email"], row["company"])
    except Exception as exc:  # noqa: BLE001
        send_outreach.update_contact(s, row["id"], outreach_status="error", note=f" | drafts.send: {str(exc)[:200]}")
        log.error("draft [%s]: %s", row["id"], exc)
    s.commit()
    s.close()


def main(argv: list[str] | None = None) -> int:
    argparse.ArgumentParser(
        description="Sends emails to decision makers during business hours, every 5-10 min "
        "(stops if data/outreach/STOP_SENDER exists)"
    ).parse_args(argv)
    gap = random.uniform(300, 600)
    while not STOP.exists():
        s = SessionLocal()
        try:
            with_email = (C.email.is_not(None), C.email_subject.is_not(None))
            drafts = list(s.scalars(select(C.id).where(C.outreach_status == "ready_draft", *with_email).order_by(C.id)))
            # drafts already created in Gmail that must be sent as-is (avoids duplicating them)
            draft_nxt = (
                s.execute(
                    select(C.id, C.gmail_draft_id, C.email, C.company, C.full_name)
                    .where(C.outreach_status == "to_schedule", C.gmail_draft_id.is_not(None))
                    .order_by(C.updated_at, C.id)
                    .limit(1)
                )
                .mappings()
                .first()
            )
            nxt = s.scalar(
                select(C.id).where(C.outreach_status == "ready", *with_email).order_by(C.updated_at, C.id).limit(1)
            )
            last = s.scalar(select(func.max(C.sent_at)))
        finally:
            s.close()
        if drafts:
            send_outreach.main(["--ids", ",".join(map(str, drafts)), "--limit", str(len(drafts))])
        now_local = datetime.now()
        in_hours = OPEN <= now_local.time() <= CLOSE
        if (draft_nxt or nxt) and in_hours:
            elapsed = (datetime.now(UTC) - last).total_seconds() if last else 10**9
            if elapsed >= gap:
                if draft_nxt:
                    _send_existing_draft(draft_nxt)
                else:
                    send_outreach.main(["--ids", str(nxt), "--limit", "1"])
                gap = random.uniform(300, 600)
                log.info("next send no sooner than %.1f min", gap / 60)
                continue
        time.sleep(60)
    log.info("sender_loop stopped")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
