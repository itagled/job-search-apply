#!/usr/bin/env python3
"""Extract from Gmail and Calendar what may be part of a job application lifecycle.

Leaves JSON files for an agent to classify (does not write to the DB):
    data/outreach/lifecycle/jobs.json          applications (canonical id, company, role, domains, contacts)
    data/outreach/lifecycle/gmail_NN.json      candidate emails in batches (trimmed text)
    data/outreach/lifecycle/calendar.json      calendar events for the period

    jobradar scan-email [--since 2026/09/20] [--batch 40]    # by default, the last 30 days

Email filter (any): sender from an applied company domain or an ATS,
subject/snippet with application/interview/rejection words, thread where the user wrote
to a decision maker, or LinkedIn message/invitation notification. Job alert notifications are excluded.
"""

from __future__ import annotations

import argparse
import html
import json
import logging
import re
from collections import defaultdict
from datetime import UTC, datetime, timedelta

from sqlalchemy import and_, or_, select
from sqlalchemy.orm import Session

from jobradar import config
from jobradar.db.applications import first_per_posting
from jobradar.db.models import ApplicationContact, JobPost
from jobradar.db.session import SessionLocal
from jobradar.gapi import auth as gauth
from jobradar.gapi import calendar as gcal
from jobradar.gapi import gmail
from jobradar.text import host

log = logging.getLogger(__name__)

OUT = config.DATA_DIR / "outreach" / "lifecycle"
ATS = (
    "ashbyhq.com",
    "greenhouse.io",
    "greenhouse-mail.io",
    "lever.co",
    "workablemail.com",
    "workable.com",
    "zohorecruit.com",
    "zoho.com",
    "teamtailor.com",
    "teamtailor-mail.com",
    "rippling.com",
    "rippling-mail.com",
    "hiringroom.com",
    "recruitee.com",
    "gupy.io",
    "gupy.com.br",
    "bamboohr.com",
    "smartrecruiters.com",
    "myworkday.com",
    "workday.com",
    "jobvite.com",
    "breezy.hr",
    "recruitcrm.io",
    "applytojob.com",
    "jazzhr.com",
    "manatal.com",
    "careers-page.com",
    "wellfound.com",
    "angel.co",
    "arc.dev",
    "torre.ai",
    "torre.co",
    "getonbrd.com",
    "getonboard.com",
    "himalayas.app",
    "calendly.com",
    "zoom.us",
    "hirevire.com",
    "testgorilla.com",
    "hireflix.com",
    "willo.video",
    "vidcruiter.com",
    "pinpointhq.com",
    "personio.de",
    "personio.com",
    "comeet.co",
    "dover.com",
    "gem.com",
    "notion.so",
    "typeform.com",
    "hubspot.com",
    "remotive.com",
    "puentetalent.com",
    "genoma.work",
    "trabajando.com",
    "laborum.cl",
    "bumeran.com",
    "computrabajo.com",
    "indeed.com",
    "glassdoor.com",
    "otta.com",
    "welcometothejungle.com",
    "ycombinator.com",
    "workatastartup.com",
    "deel.com",
    "remote.com",
    "oysterhr.com",
)
KEYWORDS = re.compile(
    r"applica|applying|applied|postula|candidat|interview|entrevista|assessment|evaluaci|prueba t[eé]cnica|"
    r"next step|pr[oó]ximos pasos|screening|schedule|agendar|agenda|reuni[oó]n|meeting|call with|llamada|"
    r"unfortunately|lamentablemente|not moving forward|no avanzar|otro candidato|other candidates|"
    r"offer|oferta|thank you for (your )?interest|gracias por (tu|su) inter[eé]s|your profile|tu perfil|"
    r"video|loom|vocaroo|test|challenge|desaf[ií]o|hiring|recruit|reclut|talent",
    re.I,
)
EXCLUDE_FROM = re.compile(
    r"jobalerts-noreply@linkedin|jobs-noreply@linkedin|jobs-listings@linkedin|"
    r"notifications-noreply@linkedin\.com.*job|news@|newsletter|noreply@medium|"
    r"@substack|digest",
    re.I,
)
LINKEDIN_KEEP = re.compile(
    r"messaging-digest|messages-noreply|inmail|invitations@linkedin|"
    r"hit-reply|member@linkedin|sent you a message|te envi[oó] un mensaje|"
    r"accepted your invitation|acept[oó] tu invitaci[oó]n|replied|respondi",
    re.I,
)


def jobs_reference(s: Session) -> list[dict]:
    """Applications (applied or half-done), one per posting, with their contacts and email domains."""
    rows = first_per_posting(
        s.execute(
            select(
                JobPost.id,
                JobPost.external_id,
                JobPost.company,
                JobPost.title,
                JobPost.post_url,
                JobPost.status,
                JobPost.source_metadata,
            )
            .where(
                or_(
                    JobPost.status == "applied",
                    and_(JobPost.has_metadata("application_pending"), JobPost.status != "discarded"),
                )
            )
            .order_by(JobPost.id)
        ).mappings()
    )
    ids: dict[str, set[int]] = defaultdict(set)
    for jid, ext in s.execute(
        select(JobPost.id, JobPost.external_id).where(JobPost.external_id.in_([r["external_id"] for r in rows]))
    ):
        ids[ext].add(jid)
    ac = ApplicationContact
    contacts = (
        s.execute(
            select(
                ac.job_post_id, ac.company, ac.company_domain, ac.full_name, ac.email, ac.outreach_status, ac.sent_at
            )
        )
        .mappings()
        .all()
    )
    out = []
    for r in rows:
        m = r["source_metadata"] or {}
        pending = m.get("application_pending")
        pend_url = pending.get("url") if isinstance(pending, dict) else None
        cs = [c for c in contacts if c["job_post_id"] in ids[r["external_id"]]]
        domains = {
            d
            for d in [
                *(c["company_domain"] for c in cs),
                *((c["email"] or "").split("@")[-1] for c in cs if c["email"]),
                host(pend_url),
            ]
            if d
        }
        out.append(
            {
                "job_post_id": min(ids[r["external_id"]]),
                "external_id": r["external_id"],
                "company": r["company"],
                "title": r["title"],
                "status": r["status"],
                "applied_via": m.get("applied_via"),
                "applied_at": m.get("applied_at"),
                "post_url": r["post_url"],
                "apply_url": pend_url,
                "domains": sorted(domains),
                "hiring_team": m.get("hiring_team"),
                "recruiter": m.get("recruiter_name"),
                "contacts": [
                    {
                        "name": c["full_name"],
                        "email": c["email"],
                        "status": c["outreach_status"],
                        "sent_at": c["sent_at"].isoformat() if c["sent_at"] else None,
                    }
                    for c in cs
                ],
            }
        )
    return out


def scan_gmail(since: str, jobs: list[dict], s: Session) -> list[dict]:
    svc = gmail.service()
    company_domains = {d for j in jobs for d in j["domains"]}
    sent_ids = s.scalars(
        select(ApplicationContact.gmail_message_id).where(ApplicationContact.gmail_message_id.is_not(None))
    ).all()
    our_threads = set()
    for mid in sent_ids:
        try:
            our_threads.add(
                gauth.execute(svc.users().messages().get(userId="me", id=mid, format="minimal"))["threadId"]
            )
        except Exception:  # noqa: BLE001
            pass
    ids, token = [], None
    q = f"after:{since} -in:sent -in:drafts -in:chats"
    while True:
        res = gauth.execute(svc.users().messages().list(userId="me", q=q, maxResults=500, pageToken=token))
        ids += [m["id"] for m in res.get("messages", [])]
        token = res.get("nextPageToken")
        if not token:
            break
    keep = []
    for mid in ids:
        meta = gauth.execute(
            svc.users()
            .messages()
            .get(userId="me", id=mid, format="metadata", metadataHeaders=["From", "Subject", "Date"])
        )
        frm, subj = gmail.header(meta, "From"), gmail.header(meta, "Subject")
        snippet = html.unescape(meta.get("snippet", ""))
        addr = (re.search(r"<([^>]+)>", frm) or re.search(r"(\S+@\S+)", frm) or [None, ""])[1].lower()
        dom = addr.split("@")[-1]
        base = ".".join(dom.split(".")[-2:])
        is_li = dom.endswith("linkedin.com")
        reason = None
        if meta["threadId"] in our_threads:
            reason = "thread_outreach"
        elif is_li:
            if LINKEDIN_KEEP.search(frm + " " + subj + " " + snippet) and not EXCLUDE_FROM.search(frm):
                reason = "linkedin_msg"
        elif EXCLUDE_FROM.search(frm):
            reason = None
        elif dom in company_domains or base in company_domains:
            reason = "company_domain"
        elif any(dom == a or dom.endswith("." + a) for a in ATS):
            reason = "ats"
        elif KEYWORDS.search(subj) or KEYWORDS.search(snippet):
            reason = "keyword"
        if reason:
            keep.append((mid, reason))
    msgs = []
    for mid, reason in keep:
        full = gauth.execute(svc.users().messages().get(userId="me", id=mid, format="full"))
        msgs.append(
            {
                "gmail_message_id": mid,
                "gmail_thread_id": full["threadId"],
                "reason": reason,
                "date": datetime.fromtimestamp(int(full["internalDate"]) / 1000, tz=UTC).isoformat(),
                "from": gmail.header(full, "From"),
                "to": gmail.header(full, "To"),
                "subject": gmail.header(full, "Subject"),
                "labels": full.get("labelIds", []),
                "body": gmail.plain_text(full["payload"])[:3000],
            }
        )
    log.info("gmail: %d emails since %s; %d candidates", len(ids), since, len(msgs))
    return sorted(msgs, key=lambda m: m["date"])


def scan_calendar(since: str) -> list[dict]:
    t0 = datetime.strptime(since, "%Y/%m/%d").replace(tzinfo=UTC)
    t1 = datetime.now(UTC) + timedelta(days=45)
    items = gcal.list_events(time_min=t0.isoformat(), time_max=t1.isoformat(), max_results=250)
    out = [
        {
            "calendar_event_id": e.get("id"),
            "summary": e.get("summary"),
            "status": e.get("status"),
            "start": (e.get("start") or {}).get("dateTime") or (e.get("start") or {}).get("date"),
            "end": (e.get("end") or {}).get("dateTime") or (e.get("end") or {}).get("date"),
            "organizer": (e.get("organizer") or {}).get("email"),
            "attendees": [a.get("email") for a in e.get("attendees", [])],
            "description": re.sub(r"<[^>]+>", " ", html.unescape(e.get("description") or ""))[:1200],
            "location": e.get("location"),
            "hangout": e.get("hangoutLink"),
        }
        for e in items
    ]
    log.info("calendar: %d events", len(out))
    return out


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--since",
        default=(datetime.now(UTC) - timedelta(days=30)).strftime("%Y/%m/%d"),
        help="first day to read, YYYY/MM/DD (default: 30 days ago)",
    )
    ap.add_argument("--batch", type=int, default=40)
    ap.add_argument("--reuse-jobs", action="store_true", help="use existing jobs.json (faster)")
    args = ap.parse_args(argv)
    OUT.mkdir(parents=True, exist_ok=True)
    for f in OUT.glob("gmail_*.json"):
        f.unlink()
    s = SessionLocal()
    jp = OUT / "jobs.json"
    jobs = json.loads(jp.read_text(encoding="utf-8")) if args.reuse_jobs and jp.exists() else jobs_reference(s)
    (OUT / "jobs.json").write_text(json.dumps(jobs, ensure_ascii=False, indent=1, default=str), encoding="utf-8")
    log.info("jobs: %d applications", len(jobs))
    msgs = scan_gmail(args.since, jobs, s)
    for i in range(0, len(msgs), args.batch):
        (OUT / f"gmail_{i // args.batch:02d}.json").write_text(
            json.dumps(msgs[i : i + args.batch], ensure_ascii=False, indent=1), encoding="utf-8"
        )
    try:
        cal = scan_calendar(args.since)
    except Exception as exc:  # noqa: BLE001
        log.error("calendar: %s", exc)
        cal = []
    (OUT / "calendar.json").write_text(json.dumps(cal, ensure_ascii=False, indent=1), encoding="utf-8")
    s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
