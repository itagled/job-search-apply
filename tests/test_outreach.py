"""Outreach commands on SQLite: application outcomes, contacts, events, sending and selection."""

from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import select

from jobradar.db.models import ApplicationContact, ApplicationEvent, JobPost
from jobradar.db.session import get_session
from jobradar.outreach import app_events, mark_application, save_contact, send_outreach

NOW = datetime.now(UTC)


@pytest.fixture
def s(sqlite_db):
    with get_session() as session:
        yield session


def _contact(**kw) -> ApplicationContact:
    return ApplicationContact(email_subject="Hello", email_body="Body", **kw)


def test_record_outcome_updates_every_row_of_the_posting(s):
    s.add_all(
        [
            JobPost(source="a", external_id="x1", company="Acme", title="GTM", source_metadata={"k": 1}),
            JobPost(source="b", external_id="x1", company="Acme", title="GTM"),
            JobPost(source="a", external_id="x2", company="Beta", title="Ops", status="applied"),
        ]
    )
    s.commit()
    res = mark_application.record_outcome(s, external_id="x1", status="applied", via="ashby", note="sent")
    s.commit()
    assert res["rows"] == 2 and res["meta_keys"] == ["applied_at", "applied_via", "note"]
    rows = s.scalars(select(JobPost).where(JobPost.external_id == "x1").order_by(JobPost.id)).all()
    assert [r.status for r in rows] == ["applied", "applied"]
    assert rows[0].source_metadata["k"] == 1 and rows[1].source_metadata["applied_via"] == "ashby"

    x2 = s.scalar(select(JobPost.id).where(JobPost.external_id == "x2"))
    assert mark_application.record_outcome(s, job_id=x2, status="closed", note="gone")["rows"] == 0  # never undone
    mark_application.record_outcome(s, external_id="x1", status="pending", reason="captcha", url="https://apply")
    s.commit()
    s.expire_all()
    assert s.get(JobPost, x2).status == "applied"
    assert {r.status for r in rows} == {"applied"} and rows[0].source_metadata["application_pending"][
        "reason"
    ] == "captcha"
    assert mark_application.record_outcome(s, external_id="missing", status="applied") is None


def test_save_contact_inserts_updates_and_leaves_sent_rows_alone(s):
    first = {"company": "Acme", "email": "Ana@Acme.example", "full_name": "Ana", "raw": {"from": "apollo"}}
    assert save_contact.save(s, first).startswith("inserted row")
    assert save_contact.save(s, {"company": "ACME", "email": "ana@acme.example", "title": "VP"}).startswith("updated")
    row = s.scalars(select(ApplicationContact)).one()
    assert (row.company, row.title, row.full_name, row.raw, row.outreach_status) == ("ACME", "VP", None, {}, "pending")
    row.outreach_status = "sent"
    assert save_contact.save(s, first) == f"unchanged (row {row.id} is already sent)"
    assert save_contact.save(s, {"company": "Beta", "linkedin_url": "https://li.example/bo"}).startswith("inserted")
    assert save_contact.save(s, {"company": "beta", "linkedin_url": "https://li.example/bo"}).startswith("updated")
    with pytest.raises(ValueError):
        save_contact.save(s, {"email": "x@y.example"})


def test_events_point_to_the_canonical_posting_and_load_once(s):
    a = JobPost(source="a", external_id="x1", company="Acme", title="GTM")
    b = JobPost(source="b", external_id="x1", company="Acme", title="GTM")
    s.add_all([a, b])
    s.flush()
    ev = {
        "job_post_id": b.id,
        "event_type": "email_reply",
        "occurred_at": "2026-10-20T10:00:00Z",
        "dedupe_key": "gmail:1",
        "source": "gmail",
        "raw": {"day": datetime(2026, 10, 20)},
    }
    assert app_events.add(s, ev) and not app_events.add(s, ev)
    row = s.scalars(select(ApplicationEvent)).one()
    assert (row.job_post_id, row.job_external_id, row.company) == (a.id, "x1", "Acme")
    assert row.occurred_at == datetime(2026, 10, 20, 10, tzinfo=UTC) and row.raw == {"day": "2026-10-20 00:00:00"}
    assert app_events.canonical(s, None) == app_events.canonical(s, 10**9) == (None, None, None)


def test_backfill_turns_what_the_db_knows_into_events_once(sqlite_db, capsys):
    with get_session() as s:
        s.add_all(
            [
                JobPost(
                    source="a",
                    external_id="x1",
                    company="Acme",
                    title="GTM",
                    status="applied",
                    source_metadata={
                        "applied_at": "2026-10-01T12:00:00+00:00",
                        "applied_via": "LinkedIn + linkedin message",
                    },
                ),
                JobPost(source="b", external_id="x1", company="Acme", title="GTM", status="applied"),
                JobPost(
                    source="a",
                    external_id="x2",
                    company="Beta",
                    title="Ops",
                    source_metadata={
                        "application_pending": {"reason": "captcha", "note": "missing", "url": "https://b"}
                    },
                ),
                JobPost(
                    source="a",
                    external_id="x3",
                    company="Gamma",
                    title="Growth",
                    source_metadata={"rejected_at": "2026-10-05T00:00:00Z", "stage": "first call"},
                ),
                JobPost(source="a", external_id="x4", company="Delta", title="Ops"),
            ]
        )
        s.add(_contact(company="Acme", email="ana@acme.example", outreach_status="sent", sent_at=NOW))
        s.commit()
    app_events.backfill()
    assert json.loads(capsys.readouterr().out) == {"new_events": 6}
    app_events.backfill()
    assert json.loads(capsys.readouterr().out) == {"new_events": 0}
    with get_session() as s:
        kinds = sorted(s.scalars(select(ApplicationEvent.event_type)))
    assert kinds == [
        "application_pending",
        "applied",
        "email_sent",
        "interview_requested",
        "linkedin_message_sent",
        "rejected",
    ]


def test_import_skips_invalid_events_and_repeated_ones(sqlite_db, tmp_path, capsys):
    path = tmp_path / "events.json"
    good = {"event_type": "email_reply", "occurred_at": "2026-10-20", "dedupe_key": "k1", "source": "gmail"}
    no_source = {**good, "dedupe_key": "k2", "source": None}
    path.write_text(json.dumps([good, no_source, {"event_type": "other"}, good]), encoding="utf-8")
    app_events.import_file(str(path))
    assert json.loads(capsys.readouterr().out) == {"file": str(path), "new": 1, "invalid": 2, "already_present": 1}


def test_send_outreach_dry_run_flags_missing_cvs_and_duplicates(sqlite_db, tmp_path, capsys):
    cv = tmp_path / "cv.pdf"
    cv.write_bytes(b"%PDF-1.4")
    with get_session() as s:
        s.add_all(
            [
                _contact(company="Acme", email="ana@acme.example", cv_file=str(cv), outreach_status="ready"),
                _contact(
                    company="Beta", email="bo@beta.example", cv_file=str(tmp_path / "no.pdf"), outreach_status="ready"
                ),
                _contact(company="Gamma", email="Zoe@delta.example", cv_file=str(cv), outreach_status="ready_draft"),
                _contact(company="Delta", email="zoe@delta.example", outreach_status="sent", sent_at=NOW),
                ApplicationContact(
                    company="Eps", email="e@eps.example", outreach_status="ready"
                ),  # no subject: not ready
            ]
        )
        s.commit()
        acme = s.scalar(select(ApplicationContact.id).where(ApplicationContact.company == "Acme"))
        assert [r["company"] for r in send_outreach.pending(s, [], 50)] == ["Acme", "Beta", "Gamma"]
        assert [r["company"] for r in send_outreach.pending(s, [acme], 50)] == ["Acme"]
    send_outreach.main(["--dry-run"])
    out = json.loads(capsys.readouterr().out)
    assert (out["sent"], out["drafts"], out["skipped"], out["errors"]) == (0, 0, 1, 1)
    with get_session() as s:
        rows = {c.company: c for c in s.scalars(select(ApplicationContact))}
    assert rows["Acme"].outreach_status == "ready"
    assert (rows["Beta"].outreach_status, rows["Beta"].notes) == ("error", " | CV missing")
    assert (rows["Gamma"].outreach_status, rows["Gamma"].notes) == ("skipped", " | duplicate")


def test_scheduled_drafts_are_sent_and_vanished_ones_marked(sqlite_db, monkeypatch, capsys):
    from jobradar.gapi import gmail
    from jobradar.outreach import send_drafts, sender_loop

    def fake_send_draft(draft_id, *, svc=None):
        if draft_id == "gone":
            raise RuntimeError("HttpError 404 notFound")
        return {"id": f"msg-{draft_id}"}

    monkeypatch.setattr(gmail, "service", lambda *a, **k: object())
    monkeypatch.setattr(gmail, "send_draft", fake_send_draft)
    with get_session() as s:
        s.add_all(
            [
                _contact(company="Acme", email="a@acme.example", outreach_status="to_schedule", gmail_draft_id="d1"),
                _contact(company="Beta", email="b@beta.example", outreach_status="to_schedule", gmail_draft_id="gone"),
                _contact(company="Gamma", email="g@gamma.example", outreach_status="to_schedule", gmail_draft_id="d3"),
            ]
        )
        s.commit()
        gamma = s.execute(select(ApplicationContact).where(ApplicationContact.company == "Gamma")).scalar_one()
        draft = {"id": gamma.id, "gmail_draft_id": "gone", "full_name": None, "email": gamma.email, "company": "Gamma"}
    sender_loop._send_existing_draft(draft)  # the loop's error path
    send_drafts.main(["--gap-min", "0", "--gap-max", "0"])
    out = json.loads(capsys.readouterr().out)
    assert (out["sent"], out["already_scheduled"], out["errors"]) == (1, 1, 0)
    with get_session() as s:
        rows = {c.company: c for c in s.scalars(select(ApplicationContact))}
    assert (rows["Acme"].outreach_status, rows["Acme"].gmail_message_id) == ("sent", "msg-d1")
    assert NOW - timedelta(minutes=1) < rows["Acme"].sent_at <= datetime.now(UTC)
    assert rows["Beta"].outreach_status == "scheduled_in_gmail"
    assert rows["Gamma"].outreach_status == "error" and rows["Gamma"].notes.startswith(" | drafts.send: HttpError")


def test_decision_makers_pick_one_posting_per_company(sqlite_db, tmp_path, capsys):
    from jobradar.outreach import select_candidates

    def post(ext: str, company: str, title: str, score: int, **kw) -> JobPost:
        return JobPost(
            source="src",
            external_id=ext,
            company=company,
            title=title,
            fit_score=score,
            fit_json=kw.pop("fit_json", {"eligible": "yes"}),
            posted_at=kw.pop("posted_at", NOW),
            **kw,
        )

    with get_session() as s:
        s.add_all(
            [
                post("p1", "Acme", "GTM Engineer", 80),
                post("p2", "Acme Inc.", "GTM Engineer II", 70),  # same company
                post("p3", "Beta", "Account Executive", 90),  # outreach.exclude_titles of the profile
                post("p4", "Gamma", "Growth Engineer", 75, fit_json={"eligible": "no"}),
                post("p5", "Delta", "RevOps Engineer", 75),
                post("p6", "Delta", "GTM Engineer", 50, status="applied"),
                post("p7", "Eps", "GTM Engineer", 85, source_metadata={"application_pending": {"reason": "captcha"}}),
                post("p8", "Zeta", "GTM Engineer", 85, posted_at=NOW - timedelta(days=200)),
                post(
                    "p9",
                    "Theta",
                    "GTM Engineer",
                    60,
                    posted_at=None,
                    source_metadata={"src": {"apply_url": "https://apply.example/9"}},
                ),
            ]
        )
        s.commit()
    out = tmp_path / "to_apply.json"
    select_candidates.main(["--out", str(out)])
    summary = json.loads(capsys.readouterr().out)
    assert summary["discarded"] == {
        "title_excluded": 1,
        "not_eligible": 1,
        "company_already_applied": 1,
        "duplicate_company": 1,
    }
    picked = json.loads(out.read_text(encoding="utf-8"))
    assert [(c["external_id"], c["apply_url"]) for c in picked] == [("p1", None), ("p9", "https://apply.example/9")]


def test_the_email_scan_reference_has_one_entry_per_application(s):
    from jobradar.outreach import gmail_scan

    new = JobPost(source="a", external_id="x1", company="Acme", title="GTM")
    s.add(new)
    s.flush()
    s.add_all(
        [
            JobPost(
                source="b",
                external_id="x1",
                company="Acme",
                title="GTM",
                status="applied",
                source_metadata={"applied_via": "ashby", "hiring_team": [{"name": "Ana"}], "recruiter_name": "Bo"},
            ),
            JobPost(
                source="a",
                external_id="x2",
                company="Beta",
                title="Ops",
                source_metadata={"application_pending": {"url": "https://www.jobs.beta.example/apply"}},
            ),
            JobPost(
                source="a",
                external_id="x3",
                company="Gamma",
                title="Ops",
                status="discarded",
                source_metadata={"application_pending": {"url": "https://gamma.example"}},
            ),
            _contact(
                company="Acme",
                job_post_id=new.id,
                company_domain="acme.example",
                email="ana@acme-mail.example",
                outreach_status="sent",
                sent_at=datetime(2026, 10, 1, tzinfo=UTC),
            ),
        ]
    )
    s.commit()
    ref = {j["external_id"]: j for j in gmail_scan.jobs_reference(s)}
    assert set(ref) == {"x1", "x2"}
    assert ref["x1"]["job_post_id"] == new.id and ref["x1"]["status"] == "applied"
    assert (ref["x1"]["applied_via"], ref["x1"]["recruiter"], ref["x1"]["hiring_team"]) == (
        "ashby",
        "Bo",
        [{"name": "Ana"}],
    )
    assert ref["x1"]["domains"] == ["acme-mail.example", "acme.example"]
    assert ref["x1"]["contacts"] == [
        {"name": None, "email": "ana@acme-mail.example", "status": "sent", "sent_at": "2026-10-01T00:00:00+00:00"}
    ]
    assert (ref["x2"]["apply_url"], ref["x2"]["domains"]) == (
        "https://www.jobs.beta.example/apply",
        ["jobs.beta.example"],
    )
