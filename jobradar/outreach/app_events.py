#!/usr/bin/env python3
"""Lifecycle of applications (table ``application_event``).

    jobradar events backfill          # loads what is already in the DB
    jobradar events import FILE.json  # loads classified events (Gmail/Calendar/LinkedIn)
    jobradar events status            # number of applications per stage

Each event is deduplicated by ``dedupe_key`` (re-running does not duplicate). ``job_post_id``
is normalized to the canonical job posting row (lowest id among those sharing external_id).
Import format: list of objects with the columns of ``application_event`` (``event_type``,
``occurred_at``, ``dedupe_key`` and ``source`` are required; events without them are skipped).
``raw.action`` turns an event into a task of the status page (``"reply"``: a reply to send).
"""

from __future__ import annotations

import argparse
import json
from collections import Counter
from pathlib import Path

from sqlalchemy import func, or_, select
from sqlalchemy.orm import Session

from jobradar.dates import parse_datetime
from jobradar.db import applications
from jobradar.db.models import ApplicationContact, ApplicationEvent, JobPost
from jobradar.db.session import SessionLocal, insert_for

COLS = (
    "job_post_id",
    "job_external_id",
    "company",
    "event_type",
    "direction",
    "channel",
    "occurred_at",
    "scheduled_for",
    "contact_name",
    "contact_email",
    "application_contact_id",
    "subject",
    "summary",
    "gmail_message_id",
    "gmail_thread_id",
    "calendar_event_id",
    "url",
    "source",
    "confidence",
    "dedupe_key",
    "raw",
)


def canonical(s: Session, job_post_id: int | None) -> tuple[int | None, str | None, str | None]:
    """(canonical id, external_id, company) for any job_post_id."""
    if job_post_id is None:
        return None, None, None
    row = s.execute(select(JobPost.external_id, JobPost.company).where(JobPost.id == job_post_id)).first()
    if not row:
        return None, None, None
    cid = s.scalar(select(func.min(JobPost.id)).where(JobPost.external_id == row.external_id))
    return cid, row.external_id, row.company


def add(s: Session, ev: dict) -> bool:
    """Inserts an event; False if it already existed (same dedupe_key)."""
    ev = dict(ev)
    cid, ext, emp = canonical(s, ev.get("job_post_id"))
    ev["job_post_id"] = cid
    ev.setdefault("job_external_id", ext)
    ev["company"] = ev.get("company") or emp
    ev["raw"] = json.loads(json.dumps(ev.get("raw") or {}, ensure_ascii=False, default=str))
    for when in ("occurred_at", "scheduled_for"):
        ev[when] = parse_datetime(ev.get(when))
    vals = {c: ev.get(c) for c in COLS}
    stmt = insert_for(s)(ApplicationEvent).values(**vals).on_conflict_do_nothing(index_elements=["dedupe_key"])
    return bool(s.execute(stmt).rowcount)


def backfill() -> None:
    s = SessionLocal()
    added = 0
    rows = s.execute(
        select(
            JobPost.id,
            JobPost.external_id,
            JobPost.company,
            JobPost.title,
            JobPost.post_url,
            JobPost.status,
            JobPost.updated_at,
            JobPost.source_metadata.label("m"),
        )
        .where(
            or_(
                JobPost.status == "applied",
                *(JobPost.has_metadata(k) for k in ("application_pending", "rejected_at", "stage")),
            )
        )
        .order_by(JobPost.id)
    ).mappings()
    for j in applications.first_per_posting(rows.all()):
        m = j["m"] or {}
        base = {"job_post_id": j["id"], "source": "db_backfill", "confidence": "high"}
        via = m.get("applied_via") or ""
        if j["status"] == "applied":
            channel = "linkedin" if "linkedin" in via.lower() else "ats"
            added += add(
                s,
                {
                    **base,
                    "event_type": "applied",
                    "direction": "outbound",
                    "channel": channel,
                    "occurred_at": m.get("applied_at") or j["updated_at"],
                    "summary": (via + " | " + (m.get("note") or ""))[:1000],
                    "url": j["post_url"],
                    "dedupe_key": f"applied:{j['external_id']}",
                },
            )
            low = via.lower()
            if "linkedin message" in low or "dm" in low.split():
                added += add(
                    s,
                    {
                        **base,
                        "event_type": "linkedin_message_sent",
                        "direction": "outbound",
                        "channel": "linkedin",
                        "occurred_at": m.get("applied_at") or j["updated_at"],
                        "summary": via[:500],
                        "dedupe_key": f"li_msg_backfill:{j['external_id']}",
                    },
                )
            if "linkedin invit" in low:  # invite or invitation
                added += add(
                    s,
                    {
                        **base,
                        "event_type": "linkedin_invite_sent",
                        "direction": "outbound",
                        "channel": "linkedin",
                        "occurred_at": m.get("applied_at") or j["updated_at"],
                        "summary": via[:500],
                        "dedupe_key": f"li_inv_backfill:{j['external_id']}",
                    },
                )
        elif j["status"] != "discarded" and m.get("application_pending"):
            p = m["application_pending"]
            added += add(
                s,
                {
                    **base,
                    "event_type": "application_pending",
                    "direction": "internal",
                    "channel": "ats",
                    "occurred_at": p.get("at") or j["updated_at"],
                    "summary": f"{p.get('reason')}: {p.get('note')}"[:1000],
                    "url": p.get("url") or j["post_url"],
                    "dedupe_key": f"pending:{j['external_id']}",
                },
            )
        if m.get("rejected_at") or m.get("rejected"):
            added += add(
                s,
                {
                    **base,
                    "event_type": "rejected",
                    "direction": "inbound",
                    "channel": "email",
                    "occurred_at": m.get("rejected_at") or j["updated_at"],
                    "summary": "rejection recorded in job_post",
                    "dedupe_key": f"rejected_meta:{j['external_id']}",
                },
            )
        if m.get("stage"):
            added += add(
                s,
                {
                    **base,
                    "event_type": "interview_requested",
                    "direction": "inbound",
                    "channel": "email",
                    "occurred_at": j["updated_at"],
                    "summary": str(m["stage"])[:500],
                    "dedupe_key": f"stage_meta:{j['external_id']}",
                },
            )
        if m.get("pendiente"):
            added += add(
                s,
                {
                    **base,
                    "event_type": "assessment_requested",
                    "direction": "inbound",
                    "channel": "ats",
                    "occurred_at": j["updated_at"],
                    "summary": str(m["pendiente"])[:500],
                    "dedupe_key": f"pendiente_meta:{j['external_id']}",
                },
            )
    ac = ApplicationContact
    contacts = (
        s.execute(
            select(
                ac.id,
                ac.job_post_id,
                ac.company,
                ac.full_name,
                ac.email,
                ac.email_subject,
                ac.gmail_message_id,
                ac.sent_at,
            ).where(ac.outreach_status == "sent", ac.sent_at.is_not(None))
        )
        .mappings()
        .all()
    )
    for c in contacts:
        added += add(
            s,
            {
                "job_post_id": c["job_post_id"],
                "company": c["company"],
                "event_type": "email_sent",
                "direction": "outbound",
                "channel": "email",
                "occurred_at": c["sent_at"],
                "contact_name": c["full_name"],
                "contact_email": c["email"],
                "application_contact_id": c["id"],
                "subject": c["email_subject"],
                "gmail_message_id": c["gmail_message_id"],
                "source": "db_backfill",
                "confidence": "high",
                "dedupe_key": f"contact_sent:{c['id']}",
            },
        )
    s.commit()
    s.close()
    print(json.dumps({"new_events": added}))


def import_file(path: str) -> None:
    data = json.loads(Path(path).read_text(encoding="utf-8"))
    s = SessionLocal()
    added = skipped = 0
    for ev in data:
        if not all(ev.get(k) for k in ("event_type", "occurred_at", "dedupe_key", "source")):
            skipped += 1
            continue
        added += add(s, ev)
    s.commit()
    s.close()
    print(
        json.dumps(
            {"file": str(path), "new": added, "invalid": skipped, "already_present": len(data) - added - skipped}
        )
    )


def status() -> None:
    with SessionLocal() as s:
        stages = Counter(a["stage"] for a in applications.application_status(s))
    print(json.dumps(dict(stages.most_common()), ensure_ascii=False))


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Events of each application's lifecycle")
    ap.add_argument("cmd", nargs="?", default="status", choices=["status", "backfill", "import"])
    ap.add_argument("file", nargs="?", help="JSON of events (for import)")
    args = ap.parse_args(argv)
    if args.cmd == "backfill":
        backfill()
    elif args.cmd == "import":
        if not args.file:
            ap.error("import needs the JSON file")
        import_file(args.file)
    else:
        status()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
