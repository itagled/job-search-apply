"""The status page queries on SQLite, with example data covering every case."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest

from jobradar.db import applications
from jobradar.db.models import ApplicationEvent, JobPost
from jobradar.db.session import get_session

NOW = datetime.now(UTC)


@pytest.fixture
def s(sqlite_db):
    with get_session() as session:
        acme = JobPost(
            source="s",
            external_id="a1",
            company="Acme",
            title="GTM Engineer",
            status="applied",
            post_url="https://jobs.example/a1",
        )
        beta = JobPost(
            source="s",
            external_id="b1",
            company="Beta",
            title="RevOps",
            status="new",
            source_metadata={
                "application_pending": {
                    "url": "https://apply.example/b",
                    "reason": "captcha",
                    "note": "captcha missing",
                    "at": "2026-10-20T10:00:00Z",
                }
            },
        )
        gamma = JobPost(source="s", external_id="c1", company="Gamma", title="Growth", status="applied")
        session.add_all([acme, beta, gamma])
        session.flush()

        def ev(post: JobPost | None, kind: str, days_ago: float, n: int, **kw) -> ApplicationEvent:
            return ApplicationEvent(
                job_post_id=post.id if post else None,
                event_type=kind,
                source="test",
                occurred_at=NOW - timedelta(days=days_ago),
                dedupe_key=f"k{n}",
                **kw,
            )

        session.add_all(
            [
                ev(acme, "applied", 10, 1),
                ev(acme, "interview_scheduled", 3, 2, scheduled_for=NOW + timedelta(days=2), summary="Call with Ana"),
                ev(acme, "assessment_requested", 5, 3, summary="Case study"),
                ev(acme, "email_reply", 2, 4, direction="inbound", gmail_thread_id="t1", summary="Let's talk"),
                ev(acme, "email_reply", 1, 5, raw={"action": "reply"}, summary="Answer the recruiter"),
                ev(beta, "application_pending", 3, 6),
                ev(gamma, "applied", 9, 7),
                ev(gamma, "interview_done", 3, 8, summary="First round"),
                ev(None, "email_sent", 1, 9, company="Delta", job_external_id="d1"),
            ]
        )
        session.commit()
        yield session


def test_interviews_tasks_and_replies(s):
    assert [r["summary"] for r in applications.upcoming_interviews(s)] == ["Call with Ana"]
    assert [(r["company"], r["latest"]) for r in applications.recent_interviews(s)] == [("Gamma", "First round")]
    tasks = {r["kind"]: r for r in applications.pending_tasks(s)}
    assert set(tasks) == {"assessment", "reply"} and tasks["assessment"]["company"] == "Acme"
    assert applications.mark_task_done(s, tasks["assessment"]["id"]) and applications.mark_task_done(
        s, tasks["assessment"]["id"]
    )
    s.commit()
    assert {r["kind"] for r in applications.pending_tasks(s)} == {"reply"}
    assert not applications.mark_task_done(s, 999_999)
    replies = applications.recent_replies(s)
    assert len(replies) == 1 and replies[0]["company"] == "Acme"


def test_incomplete_applications_and_stages(s):
    pending = applications.incomplete_applications(s)
    assert [(r["company"], r["reason"], r["at"]) for r in pending] == [
        ("Beta", "captcha", datetime(2026, 10, 20, 10, tzinfo=UTC))
    ]
    stages = {a["app_key"]: a for a in applications.application_status(s)}
    assert stages["acme|gtm engineer"]["stage"] == "interview"
    assert stages["acme|gtm engineer"]["gmail_thread_id"] == "t1"
    assert stages["beta|revops"]["stage"] == "incomplete"
    assert stages["gamma|growth"]["stage"] == "interview"
    assert stages["d1"]["stage"] == "contacted" and "d1" not in {a["app_key"] for a in applications.all_applications(s)}


def test_the_status_page_and_events_status_run_on_sqlite(s, capsys):
    from jobradar.outreach import app_events
    from jobradar.web import status

    page = status._render(s)
    assert "Acme" in page and "Call with Ana" in page and "Beta" in page
    app_events.status()
    assert json.loads(capsys.readouterr().out) == {"interview": 2, "incomplete": 1, "contacted": 1}
