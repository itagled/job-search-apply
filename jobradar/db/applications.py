"""Queries behind the status page (``jobradar web``) and ``jobradar events``.

Interviews, pending tasks, replies and the stage of every application. Portable SQLAlchemy (Postgres
and SQLite): the filters run in SQL and the grouping that Postgres did with ``distinct on``, ``FILTER``
and a view runs in Python over the events, which are a few thousand rows. Reads return plain dicts;
writes don't commit (the caller does).
"""

from __future__ import annotations

from collections import defaultdict
from collections.abc import Iterable, Mapping
from datetime import UTC, datetime, timedelta
from typing import Any

from sqlalchemy import inspect, select
from sqlalchemy.orm import Session

from jobradar.dates import parse_datetime
from jobradar.db.models import ApplicationEvent, JobPost
from jobradar.db.session import insert_for

E = ApplicationEvent
DONE_PREFIX = "web:done:"  # dedupe_key of the "done" mark of a task (stored value)
CLOSING_EVENTS = ("rejected", "withdrawn", "offer")


def _now() -> datetime:
    return datetime.now(UTC)


def _event(ev: ApplicationEvent, **extra: Any) -> dict[str, Any]:
    return {a.key: getattr(ev, a.key) for a in inspect(E).column_attrs} | extra


def first_per_posting(rows: Iterable[Mapping[str, Any]]) -> list[Mapping[str, Any]]:
    """The first row of each posting (``external_id``): with rows ordered by id, the canonical one."""
    first: dict[str, Mapping[str, Any]] = {}
    for r in rows:
        first.setdefault(r["external_id"], r)
    return list(first.values())


def _with_posting(s: Session, *where: Any) -> list[tuple[ApplicationEvent, JobPost]]:
    """Events that match ``where``, with their posting (events without a posting are left out)."""
    return list(s.execute(select(E, JobPost).join(JobPost, JobPost.id == E.job_post_id).where(*where)).tuples())


def _posting_fields(jp: JobPost) -> dict[str, Any]:
    return {"company": jp.company, "title": jp.title, "post_url": jp.post_url}


def upcoming_interviews(s: Session) -> list[dict[str, Any]]:
    """Interviews scheduled from 2 hours ago onwards: one per posting and time slot, its latest update."""
    rows = _with_posting(s, E.event_type == "interview_scheduled", E.scheduled_for >= _now() - timedelta(hours=2))
    latest: dict[tuple, tuple[ApplicationEvent, JobPost]] = {}
    for ev, jp in rows:
        key = (ev.job_post_id, ev.scheduled_for)
        if key not in latest or ev.occurred_at > latest[key][0].occurred_at:
            latest[key] = (ev, jp)
    return [_event(ev, **_posting_fields(jp)) for ev, jp in latest.values()]


def recent_interviews(s: Session) -> list[dict[str, Any]]:
    """Interviews from the last 10 days, with the latest event of each application."""
    now = _now()
    rows = _with_posting(s, E.event_type.in_(("interview_scheduled", "interview_done")))
    happened: dict[int, tuple[datetime, JobPost]] = {}
    for ev, jp in rows:
        when = ev.scheduled_for or ev.occurred_at
        if now - timedelta(days=10) <= when <= now - timedelta(hours=2):
            if ev.job_post_id not in happened or when > happened[ev.job_post_id][0]:
                happened[ev.job_post_id] = (when, jp)
    latest: dict[int, str | None] = {}
    for ev in s.scalars(select(E).where(E.job_post_id.in_(list(happened))).order_by(E.occurred_at.desc())):
        latest.setdefault(ev.job_post_id, ev.summary or ev.subject)
    out = [
        {"job_post_id": jid, "happened_at": when, "latest": latest.get(jid), "company": jp.company, "title": jp.title}
        for jid, (when, jp) in happened.items()
    ]
    return sorted(out, key=lambda r: r["happened_at"], reverse=True)


def pending_tasks(s: Session) -> list[dict[str, Any]]:
    """Requested tests or case studies and replies to send, one per posting and kind (the latest one),
    minus the ones marked done, the ones a later rejection/withdrawal/offer closed, and the tests
    already delivered."""
    candidates = [
        (ev, jp) for ev, jp in _with_posting(s) if ev.event_type == "assessment_requested" or "action" in (ev.raw or {})
    ]
    if not candidates:
        return []
    ids = {ev.job_post_id for ev, _ in candidates}
    later = defaultdict(list)
    for ev in s.scalars(
        select(E).where(E.job_post_id.in_(ids), E.event_type.in_((*CLOSING_EVENTS, "assessment_done")))
    ):
        later[ev.job_post_id].append(ev)
    done = set(s.scalars(select(E.dedupe_key).where(E.dedupe_key.like(f"{DONE_PREFIX}%"))))
    tasks: dict[tuple, tuple[ApplicationEvent, JobPost, str]] = {}
    for ev, jp in candidates:
        if f"{DONE_PREFIX}{ev.id}" in done:
            continue
        after = [x for x in later[ev.job_post_id] if x.occurred_at > ev.occurred_at]
        if any(x.event_type in CLOSING_EVENTS for x in after):
            continue
        if ev.event_type == "assessment_requested" and any(x.event_type == "assessment_done" for x in after):
            continue
        kind = (ev.raw or {}).get("action") or "assessment"
        key = (ev.job_post_id, kind)
        if key not in tasks or ev.occurred_at > tasks[key][0].occurred_at:
            tasks[key] = (ev, jp, kind)
    return [_event(ev, kind=kind, **_posting_fields(jp)) for ev, jp, kind in tasks.values()]


def recent_replies(s: Session) -> list[dict[str, Any]]:
    """The latest reply from a person (email or LinkedIn) per posting, over the last 14 days."""
    rows = _with_posting(
        s, E.event_type.in_(("email_reply", "linkedin_reply")), E.occurred_at > _now() - timedelta(days=14)
    )
    latest: dict[int, tuple[ApplicationEvent, JobPost]] = {}
    for ev, jp in rows:
        if ev.job_post_id not in latest or ev.occurred_at > latest[ev.job_post_id][0].occurred_at:
            latest[ev.job_post_id] = (ev, jp)
    return [_event(ev, company=jp.company) for ev, jp in latest.values()]


def incomplete_applications(s: Session, *, one_per_link: bool = True) -> list[dict[str, Any]]:
    """Postings with a half-done application (a manual step is missing), one per link or every row."""
    rows = s.scalars(
        select(JobPost)
        .where(JobPost.status.not_in(("applied", "discarded")), JobPost.has_metadata("application_pending"))
        .order_by(JobPost.id)
    )
    out: dict[str, dict[str, Any]] = {}
    for jp in rows:
        pending = (jp.source_metadata or {}).get("application_pending") or {}
        if not isinstance(pending, dict):
            continue
        key = (pending.get("url") if one_per_link else None) or str(jp.id)
        out.setdefault(
            key,
            {
                "id": jp.id,
                "company": jp.company,
                "title": jp.title,
                "reason": pending.get("reason"),
                "url": pending.get("url"),
                "note": pending.get("note"),
                "at": parse_datetime(pending.get("at")),
            },
        )
    return list(out.values())


def _app_key(ev: ApplicationEvent, jp: JobPost | None) -> str | None:
    """One application = one posting (company|title), else its external id, else its company."""
    if jp is not None and jp.company is not None and jp.title is not None:
        return f"{jp.company.lower()}|{jp.title.lower()}"
    if ev.job_external_id:
        return ev.job_external_id
    return f"company:{ev.company.lower()}" if ev.company else None


def _stage(a: dict[str, Any]) -> str:
    if a["offer"]:
        return "offer"
    if a["rejected"]:
        return "rejected"
    if a["interview"]:
        return "interview"
    if a["interview_requested"]:
        return "interview_requested"
    if a["assessment"]:
        return "assessment"
    if a["human_reply"]:
        return "replied"
    if a["pending_only"]:
        return "incomplete"
    return "no_reply" if a["applied_at"] else "contacted"


def application_status(s: Session) -> list[dict[str, Any]]:
    """Every application with its stage, latest movement and email thread.

    Events are grouped by application (``_app_key``); the stage follows the most advanced event
    (offer > rejection > interview > ... > applied > contacted).
    """
    rows = s.execute(select(E, JobPost).outerjoin(JobPost, JobPost.id == E.job_post_id)).tuples().all()
    groups: dict[str | None, list[tuple[ApplicationEvent, JobPost | None]]] = defaultdict(list)
    for ev, jp in rows:
        groups[_app_key(ev, jp)].append((ev, jp))
    out = []
    for key, items in groups.items():
        evs = [ev for ev, _ in items]
        types = {ev.event_type for ev in evs}
        first_post = min((ev.job_post_id for ev in evs if ev.job_post_id is not None), default=None)
        jp = next((p for ev, p in items if ev.job_post_id == first_post), None)
        applied = [ev.occurred_at for ev in evs if ev.event_type == "applied"]
        latest = max((ev for ev in evs if ev.event_type != "applied"), key=lambda ev: ev.occurred_at, default=None)
        thread = max(
            (ev for ev in evs if ev.gmail_thread_id and ev.direction == "inbound"),
            key=lambda ev: ev.occurred_at,
            default=None,
        )
        a = {
            "app_key": key,
            "job_post_id": first_post,
            "company": max((ev.company for ev in evs if ev.company), default=None),
            "applied_at": min(applied, default=None),
            "rejected": "rejected" in types,
            "offer": "offer" in types,
            "interview": bool(types & {"interview_scheduled", "interview_done"}),
            "interview_requested": "interview_requested" in types,
            "assessment": "assessment_requested" in types and not types & {"assessment_done", "withdrawn"},
            "human_reply": bool(types & {"email_reply", "linkedin_reply"}),
            "pending_only": "application_pending" in types and "applied" not in types,
            "emails_sent": sum(ev.event_type == "email_sent" for ev in evs),
            "linkedin_sent": sum(ev.event_type in ("linkedin_message_sent", "linkedin_invite_sent") for ev in evs),
            "next_interview_at": max(
                (ev.scheduled_for for ev in evs if ev.event_type == "interview_scheduled" and ev.scheduled_for),
                default=None,
            ),
            "last_event_at": max(ev.occurred_at for ev in evs),
            "title": jp.title if jp else None,
            "post_url": jp.post_url if jp else None,
            "last_summary": (latest.summary or latest.subject or latest.event_type) if latest else None,
            "last_at": latest.occurred_at if latest else None,
            "gmail_thread_id": thread.gmail_thread_id if thread else None,
        }
        a["stage"] = _stage(a)
        out.append(a)
    return out


def all_applications(s: Session) -> list[dict[str, Any]]:
    """The applications for the status table (the ones only contacted are left out)."""
    return [a for a in application_status(s) if a["stage"] != "contacted"]


def mark_task_done(s: Session, event_id: int, *, note_prefix: str = "Marked as done on the status page: ") -> bool:
    """Record (once) that the task behind event ``event_id`` was done. ``False`` if the event doesn't exist."""
    ev = s.get(E, event_id)
    if ev is None:
        return False
    values = {
        "job_post_id": ev.job_post_id,
        "job_external_id": ev.job_external_id,
        "company": ev.company,
        "event_type": "follow_up_sent" if (ev.raw or {}).get("action") else "assessment_done",
        "direction": "outbound",
        "channel": "other",
        "occurred_at": _now(),
        "summary": note_prefix + (ev.summary or "")[:200],
        "source": "status_page",
        "confidence": "high",
        "dedupe_key": f"{DONE_PREFIX}{event_id}",
        "raw": {},
    }
    s.execute(insert_for(s)(E).values(**values).on_conflict_do_nothing(index_elements=[E.dedupe_key]))
    return True
