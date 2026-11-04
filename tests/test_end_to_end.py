"""End to end on a new SQLite database, through the ``jobradar`` command and with a fake LLM."""

from __future__ import annotations

import json
from datetime import UTC, datetime

from alembic import command
from alembic.config import Config

from jobradar import cli, config, llm, profile, profile_draft
from jobradar.db import session as db

REPO = config.ROOT


def test_a_new_sqlite_database_runs_the_whole_flow(monkeypatch, tmp_path, capsys):
    from jobradar.fit import run, scorer
    from jobradar.web import status

    # the default URL: a SQLite file relative to the project, whose folder does not exist yet
    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/jobradar.db")
    monkeypatch.setattr(db, "_ENGINE", None)
    monkeypatch.setattr(db, "_SessionLocal", None)
    alembic = Config(str(REPO / "alembic.ini"))
    alembic.set_main_option("script_location", str(REPO / "migrations"))
    command.upgrade(alembic, "head")
    assert (tmp_path / "data" / "jobradar.db").is_file()

    postings = tmp_path / "postings.json"
    posting = {
        "id": "e2e-1",
        "title": "GTM Engineer",
        "company": "Acme",
        "location": "Remote LATAM",
        "url": "https://jobs.example/e2e-1",
        "description": "Build GTM automations with Clay, Python and LLM agents.",
        "posted_at": datetime.now(UTC).isoformat(),
    }
    postings.write_text(json.dumps([posting]), encoding="utf-8")
    assert cli.main(["sources", "load", "--source", "manual", str(postings)]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 1

    cv = tmp_path / "cv.md"
    cv.write_text("GTM engineer who builds automations.", encoding="utf-8")
    monkeypatch.setattr(run, "_CV_PATH", cv)
    monkeypatch.setenv("NANOGPT_API_KEY", "test-key")
    answer = {k: {"score": 80} for k in profile.load().scoring.weights}
    answer |= {"eligible": "yes", "summary": "Strong fit.", "company_name": "Acme"}
    monkeypatch.setattr(scorer.llm, "chat_json", lambda *a, **k: llm.LLMResponse(answer))
    assert cli.main(["fit", "--triage", "none"]) == 0
    assert json.loads(capsys.readouterr().out)["scored"] == 1

    assert cli.main(["mark-application", "--external-id", "e2e-1", "--status", "applied", "--via", "ats"]) == 0
    assert cli.main(["events", "backfill"]) == 0
    capsys.readouterr()
    assert cli.main(["events", "status"]) == 0
    assert json.loads(capsys.readouterr().out) == {"no_reply": 1}
    with db.get_session() as s:
        page = status._render(s)
    assert "Acme" in page and "GTM Engineer" in page


def test_what_the_user_gives_decides_what_is_searched_and_how_it_is_scored(sqlite_db, monkeypatch, tmp_path, capsys):
    from jobradar.fit import scorer
    from jobradar.sources import filters, linkedin_apify, linkedin_public
    from jobradar.sources.connectors import linkedin

    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "target.md"))
    answer = {
        "role": "Registered Nurse",
        "keywords": ["registered nurse", "enfermera"],
        "company_ideal": "Hospitals and clinics.",
        "description": "# Ideal role: Registered Nurse\n\n## What the role is\nCaring for patients.",
    }
    monkeypatch.setattr(profile_draft.llm, "chat_json", lambda *a, **k: llm.LLMResponse(answer))
    posting = tmp_path / "posting.txt"
    posting.write_text("Registered nurse for a public hospital, night shifts.", encoding="utf-8")
    assert cli.main(["profile", "from-text", str(posting)]) == 0
    assert json.loads(capsys.readouterr().out)["keywords"] == answer["keywords"]

    # the job boards are searched for those keywords, on LinkedIn and on Apify
    searched = []
    monkeypatch.setattr(linkedin_public, "search_cards", lambda keyword, **kw: searched.append(keyword) or [])
    assert list(linkedin.LinkedInPublic().search(days=7)) == [] and searched == answer["keywords"]
    payload = linkedin_apify.build_payload("registered nurse", location="Spain", date_posted="week")
    assert payload["keywords"] == "registered nurse" and payload["titleInclude"] == answer["keywords"]
    assert filters.title_in_family("Registered Nurse")[0] and not filters.title_in_family("GTM Engineer")[0]

    # and every posting is scored against the description made from the same text
    prompts = []
    monkeypatch.setattr(scorer.llm, "chat_json", lambda s, user, **k: prompts.append(user) or llm.LLMResponse({}))
    scorer.score_job("my cv", profile.load().description, {"title": "Nurse"}, model="m")
    assert "Caring for patients." in prompts[0] and "night shifts" not in prompts[0]
