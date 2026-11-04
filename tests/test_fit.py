from __future__ import annotations

import json

import pytest
from sqlalchemy import select

from jobradar import llm, profile
from jobradar.fit import jev, scorer


def _rubric() -> profile.Rubric:
    return profile.load().scoring  # the example GTM profile (see conftest)


def test_jev_asks_the_same_dimensions_as_the_scorer():
    rubric = _rubric()
    assert set(jev.questions(rubric)) - {jev.ELIGIBLE} == set(rubric.weights)
    assert all(len(q["criteria"]) == 5 for k, q in jev.questions(rubric).items() if k != jev.ELIGIBLE)


def test_combine_multiplies_identity_and_eligibility():
    rubric = _rubric()
    dims = {k: {"score": 100} for k in rubric.weights}
    assert scorer.combine(dims, "yes", rubric)["final"] == pytest.approx(100 * rubric.eligibility_factors["yes"])
    without_identity = {**dims, **{k: {"score": 0} for k in rubric.identity}}
    assert scorer.combine(without_identity, "yes", rubric)["identity_factor"] == pytest.approx(rubric.identity_floor)
    assert scorer.combine({}, "yes", rubric) is None


def test_score_job_builds_the_score(monkeypatch):
    rubric = _rubric()
    prompts = []
    fake_response = {k: {"score": 80, "reason": "x"} for k in rubric.weights}
    fake_response.update(eligible="yes", company_name="Acme", summary="fits")

    def fake_chat(system, user, **kwargs):
        prompts.append(system)
        return llm.LLMResponse(fake_response)

    monkeypatch.setattr(scorer.llm, "chat_json", fake_chat)
    res = scorer.score_job("cv", "target", {"title": "GTM Engineer"}, model="m")
    assert res.error is None and res.company_name == "Acme" and res.fit_json["eligible"] == "yes"
    assert res.fit_json["model"] == "m" and res.fit_score == round(res.fit_json["final_score"])
    assert "GTM Engineer roles" in prompts[0] and "'accessibility_fit' DOES look" in prompts[0]


def test_a_generic_profile_scores_with_the_default_rubric(monkeypatch):
    prof = profile.parse("+++\nrole = 'Data Analyst'\n[search]\nkeywords = ['data analyst']\n+++\nSQL and Python.")
    fake_response = {k: {"score": 50} for k in prof.scoring.weights}
    monkeypatch.setattr(scorer.llm, "chat_json", lambda *a, **k: llm.LLMResponse(fake_response))
    res = scorer.score_job("cv", prof.description, {"title": "Data Analyst"}, model="m", prof=prof)
    assert res.fit_json["eligible"] == "unclear" and res.fit_score == round(50 * (0.35 + 0.65 * 0.5) * 0.9)


def test_score_job_soft_fail_except_out_of_credits(monkeypatch):
    def fails(*a, **k):
        raise llm.LLMError("HTTP 500")

    monkeypatch.setattr(scorer.llm, "chat_json", fails)
    assert scorer.score_job("cv", "t", {}, model="m").error == "llm_error: HTTP 500"

    def out_of_credits(*a, **k):
        raise llm.OutOfCreditsError("402")

    monkeypatch.setattr(scorer.llm, "chat_json", out_of_credits)
    with pytest.raises(llm.OutOfCreditsError):
        scorer.score_job("cv", "t", {}, model="m")


def test_the_batch_saves_scores_and_errors_on_the_postings(sqlite_db, monkeypatch, tmp_path, capsys):
    from jobradar.db.models import JobPost
    from jobradar.db.session import get_session
    from jobradar.fit import run

    cv = tmp_path / "cv.md"
    cv.write_text("My CV", encoding="utf-8")
    monkeypatch.setattr(run, "_CV_PATH", cv)
    monkeypatch.setenv("NANOGPT_API_KEY", "test-key")

    def fake_score(cv_text, description, job, *, prof=None):
        if job["external_id"] == "bad":
            return scorer.FitResult(None, None, error="llm_error: timeout")
        return scorer.FitResult(81, "high", {"summary": "good"}, company_name="Acme")

    monkeypatch.setattr(scorer, "score_job", fake_score)
    with get_session() as s:
        s.add_all(
            [
                JobPost(source="s", external_id="ok", title="GTM", company=" ", job_description_full="A description"),
                JobPost(source="s", external_id="bad", title="GTM", company="Beta", job_description_full="Another one"),
                JobPost(source="s", external_id="blank", title="GTM", job_description_full="   "),
                JobPost(source="s", external_id="done", title="GTM", job_description_full="x", fit_status="done"),
            ]
        )
        s.commit()
    assert run.main(["--triage", "none"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["scored"], out["errors"]) == (1, 1)
    with get_session() as s:
        rows = {j.external_id: j for j in s.scalars(select(JobPost))}
    ok = rows["ok"]
    assert (ok.fit_score, ok.fit_tier, ok.fit_status, ok.fit_json, ok.company) == (
        81,
        "high",
        "done",
        {"summary": "good"},
        "Acme",
    )
    assert ok.fit_scored_at is not None and ok.fit_error is None
    assert (rows["bad"].fit_status, rows["bad"].fit_error, rows["bad"].company) == (
        "error",
        "llm_error: timeout",
        "Beta",
    )
    assert (rows["blank"].fit_status, rows["done"].fit_score) == ("pending", None)


def test_without_jev_every_posting_goes_to_the_llm(sqlite_db, monkeypatch, tmp_path, capsys):
    from jobradar.db.models import JobPost
    from jobradar.db.session import get_session
    from jobradar.fit import run

    cv = tmp_path / "cv.md"
    cv.write_text("My CV", encoding="utf-8")
    monkeypatch.setattr(run, "_CV_PATH", cv)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")

    def no_jev(*a, **k):
        raise AssertionError("Jev is not on OpenRouter")

    monkeypatch.setattr(jev, "jev_score", no_jev)
    monkeypatch.setattr(scorer, "score_job", lambda *a, **k: scorer.FitResult(70, "medium", {"summary": "ok"}))
    with get_session() as s:
        s.add(JobPost(source="s", external_id="ok", title="GTM", job_description_full="A description"))
        s.commit()
    assert run.main([]) == 0
    out = json.loads(capsys.readouterr().out)
    assert (out["scored"], out["triage"], out["llm_scored"]) == (1, "none", 1)


def test_fit_without_any_key_is_skipped(capsys):
    from jobradar.fit import run

    assert run.main([]) == 0 and capsys.readouterr().out == ""
