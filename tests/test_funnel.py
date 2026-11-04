from __future__ import annotations

import argparse
import inspect

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import sessionmaker

from jobradar.companies import run
from jobradar.companies.stages import common, enrichment, screening
from jobradar.db import session as db
from jobradar.db.models import Base, TargetCompany


@pytest.fixture
def engine(monkeypatch, tmp_path):
    """SQLite in a temporary file behind ``get_session`` and ``write_with_retry``."""
    engine = create_engine(f"sqlite:///{tmp_path / 'embudo.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "_ENGINE", engine)
    monkeypatch.setattr(db, "_SessionLocal", sessionmaker(bind=engine, autoflush=False))
    return engine


def _companies(*rows: dict) -> None:
    with db.get_session() as s:
        s.add_all(TargetCompany(**f) for f in rows)
        s.commit()


def _connection_drop(*_args):
    raise OperationalError("update", {}, Exception("connection lost"))


def test_write_with_retry_retries_if_connection_drops(engine):
    attempts = []

    def write(session):
        attempts.append(1)
        if len(attempts) == 1:
            _connection_drop()
        session.add(TargetCompany(name="Acme"))
        return "listo"

    assert db.write_with_retry(write, pause=0) == "listo" and len(attempts) == 2
    with db.get_session() as s:
        assert s.scalars(select(TargetCompany.name)).all() == ["Acme"]
    with pytest.raises(OperationalError):
        db.write_with_retry(_connection_drop, attempts=2, pause=0)


def test_screen_scores_and_advances_only_active(engine):
    _companies(
        {"name": "Payments", "industry": "fintech", "description": "SaaS de pagos para pymes", "employee_count": 40},
        {"name": "Dropped", "review_decision": "drop"},
        {"name": "Already screened", "stage": "screened"},
    )
    assert screening.screen() == {"processed": 1}
    with db.get_session() as s:
        companies = {c.name: c for c in s.scalars(select(TargetCompany))}
    assert companies["Payments"].stage == "screened" and companies["Payments"].tier_pre in common.TIERS
    assert companies["Payments"].heuristic_score is not None and companies["Payments"].stage_updated_at is not None
    assert companies["Dropped"].stage == "raw" and companies["Already screened"].heuristic_score is None


def test_backfill_linkedin_fills_only_top_missing(engine, monkeypatch):
    _companies(
        {"name": "A1", "review_decision": "promote", "fit_score": 90},
        {"name": "A2", "review_decision": "promote", "fit_score": 80},
        {"name": "A3", "review_decision": "promote", "fit_score": 10},
        {"name": "B1", "review_decision": "keep", "fit_score": 99},
    )
    found = {"A1": "https://www.linkedin.com/company/a1"}
    monkeypatch.setattr(enrichment, "fetch_company_linkedin_url", lambda name, domain: found.get(name))
    assert enrichment.backfill_company_linkedin(top=2, workers=2) == {"processed": 2, "saved": 1, "failed": 0}
    with db.get_session() as s:
        urls = dict(s.execute(select(TargetCompany.name, TargetCompany.linkedin_url)).all())
    assert urls == {"A1": found["A1"], "A2": None, "A3": None, "B1": None}


def test_process_counts_and_keeps_done_if_something_fails(monkeypatch):
    saved: list = []
    monkeypatch.setattr(common, "write_with_retry", lambda fn: fn(None))

    def save(_session, rows):
        saved.extend(rows)

    def fn(x):
        return {3: None, 4: common.Failure("no answer")}.get(x, x)

    assert common.process("sample", [1, 2, 3, 4, 5], fn, save, workers=1) == {"processed": 5, "saved": 3, "failed": 1}
    assert saved == [1, 2, 5]

    saved.clear()

    def out_of_credits(x):
        if x == 3:
            raise RuntimeError("402")
        return x

    with pytest.raises(RuntimeError):
        common.process("sample", [1, 2, 3, 4], out_of_credits, save, workers=1)
    assert saved == [1, 2]


def test_in_parallel_warms_up_with_first():
    seen: list[int] = []

    def double(x):
        seen.append(x)
        return 2 * x

    results = list(common.in_parallel(double, [1, 2, 3, 4], workers=3, warm_up=True))
    assert seen[0] == 1 and results[0] == 2 and sorted(results) == [2, 4, 6, 8]
    assert common.tiers_from("medium") == ["medium", "high"] and common.tiers_from("low") == list(common.TIERS)


def test_cli_options_match_each_stage():
    parser = run.build_parser()
    sub = next(a for a in parser._actions if isinstance(a, argparse._SubParsersAction))
    for name, sp in sub.choices.items():
        options = {a.dest for a in sp._actions if a.dest != "help"}
        assert options == set(inspect.signature(sp.get_default("func")).parameters), name


def _no_country_profile(monkeypatch, tmp_path) -> None:
    path = tmp_path / "target.md"
    path.write_text("+++\nrole = 'Data Analyst'\n[search]\nkeywords = ['data analyst']\n+++\nSQL.", encoding="utf-8")
    monkeypatch.setenv("PROFILE_PATH", str(path))


def test_the_people_searched_come_from_the_profile(monkeypatch, tmp_path):
    from jobradar import profile
    from jobradar.companies import contacts_apify as ca

    runs = []
    monkeypatch.setattr(ca, "_run_actor", lambda url, functions, **kw: runs.append(functions) or ([], 0.5))
    assert ca.fetch_company_contacts("https://www.linkedin.com/company/acme") == ([], 1.0, "fallback_ops")
    assert runs == [["25", "4", "19", "9", "12"], ["25", "4", "19", "9", "12", "18"]]  # the example profile
    assert ca._locations() == ["Chile"]
    monkeypatch.setenv("APIFY_EMPLOYEES_LOCATIONS", "Peru, Spain")
    assert ca._locations() == ["Peru", "Spain"]
    monkeypatch.delenv("APIFY_EMPLOYEES_LOCATIONS")
    _no_country_profile(monkeypatch, tmp_path)
    assert ca._locations() == [] and ca._function_ids(profile.load().companies.contact_functions) == ["9", "12"]
    with pytest.raises(profile.ProfileError):
        ca._function_ids(["Nope"])


def test_apollo_needs_a_place_to_search(sqlite_db, monkeypatch, tmp_path):
    from jobradar.companies import run

    _no_country_profile(monkeypatch, tmp_path)
    assert run.main(["extract", "--source", "apollo"]) == 1  # a clear message, not a search of the whole world


def test_searches_follow_the_country_of_the_profile(monkeypatch, tmp_path):
    from jobradar.companies import serp

    calls = []
    monkeypatch.setattr(serp.apify, "token", lambda: "token")
    monkeypatch.setattr(serp.apify, "run_sync", lambda actor, payload, **kw: calls.append(payload) or [])
    serp.fetch_description_serp("Acme", None)
    assert calls[-1] == {"keyword": "Acme Chile", "limit": "10", "country": "CL"}
    _no_country_profile(monkeypatch, tmp_path)
    serp.fetch_description_serp("Acme", None)
    assert calls[-1] == {"keyword": "Acme", "limit": "10"}
