from __future__ import annotations

import io
import json

import pytest

from jobradar import llm, profile, profile_draft

ANSWER = {
    "role": "Data Analyst",
    "keywords": ["data analyst", "analista de datos", "BI analyst"],
    "company_ideal": "Product companies with a data-driven culture.",
    "description": "# Ideal role: Data Analyst\n\n## What the role is\nTurning product data into decisions.",
}
POSTING = profile_draft.Posting(
    title="Senior Data Analyst",
    company="Acme",
    location="Madrid, Spain",
    description="We are hiring a data analyst. " * 20,
    url="https://example.com/job/1",
)


def test_the_draft_is_a_valid_profile():
    prof = profile.parse(profile_draft.render(ANSWER, POSTING))
    assert prof.role == "Data Analyst" and prof.search.keywords == ANSWER["keywords"]
    assert [(loc.name, loc.remote) for loc in prof.search.locations] == [("Spain", False)]
    assert prof.location.preset == "any"
    assert "https://example.com/job/1" in prof.reference and "We are hiring" in prof.reference
    assert "We are hiring" not in prof.description  # a record of the source, not part of the prompts


def test_from_text_writes_a_draft_without_touching_the_profile(monkeypatch, tmp_path, capsys):
    calls = []

    def fake_chat(system, user, **kwargs):
        calls.append(user)
        return llm.LLMResponse(ANSWER)

    monkeypatch.setattr(profile_draft.llm, "chat_json", fake_chat)
    monkeypatch.setattr("sys.stdin", io.StringIO("We are hiring a data analyst to own dashboards. " * 10))
    out = tmp_path / "target.draft.md"
    assert profile.main(["from-text", "-", "--notes", "remote only", "--out", str(out)]) == 0
    result = json.loads(capsys.readouterr().out)
    assert result["ok"] and result["file"] == str(out) and out.is_file() and not result["active"]
    assert "NOTES FROM THE CANDIDATE: remote only" in calls[0]


def test_a_description_in_a_few_words_becomes_the_active_profile(monkeypatch, tmp_path, capsys):
    target = tmp_path / "target.md"
    monkeypatch.setenv("PROFILE_PATH", str(target))
    monkeypatch.setattr(profile_draft.llm, "chat_json", lambda *a, **k: llm.LLMResponse(ANSWER))
    text = "Data analyst in a fintech, remote."
    monkeypatch.setattr("sys.stdin", io.StringIO(text))
    assert profile.main(["from-text"]) == 0
    assert json.loads(capsys.readouterr().out)["active"] and target.is_file()
    prof = profile.load()
    assert prof.search.keywords == ANSWER["keywords"] and text in prof.reference and text not in prof.description


def test_an_existing_profile_is_never_overwritten(monkeypatch, tmp_path, capsys):
    target = tmp_path / "target.md"
    target.write_text("mine", encoding="utf-8")
    monkeypatch.setenv("PROFILE_PATH", str(target))
    monkeypatch.setattr(profile_draft.llm, "chat_json", lambda *a, **k: llm.LLMResponse(ANSWER))
    monkeypatch.setattr("sys.stdin", io.StringIO("A data analyst role in a product company."))
    assert profile.main(["from-text"]) == 0
    assert not json.loads(capsys.readouterr().out)["active"]
    assert target.read_text(encoding="utf-8") == "mine" and (tmp_path / "target.draft.md").is_file()


def test_the_llm_only_chooses_known_job_boards_and_functions():
    answer = {**ANSWER, "niche_sources": ["portal_cargo", "made_up"], "contact_functions": ["Engineering", "Nope"]}
    prof = profile.parse(profile_draft.render(answer, POSTING))
    assert prof.search.niche_sources == ["portal_cargo"] and prof.companies.contact_functions == ["Engineering"]
    prompt = profile_draft._system_prompt()
    assert "portal_cargo" in prompt and "Product Management" in prompt


@pytest.mark.parametrize(
    "location, place",
    [
        ("Huechuraba, Santiago Metropolitan Region, Chile", ("Chile", False)),
        ("Spain", ("Spain", False)),
        ("Remote", ("Worldwide", True)),
        ("", ("Worldwide", True)),
    ],
)
def test_a_draft_searches_the_country_of_the_posting_not_its_office(location, place):
    assert profile_draft._search_place(location) == place


def test_a_profile_without_locations_searches_everywhere():
    prof = profile.parse("+++\n[search]\nkeywords = ['nurse']\n+++\nCaring for patients.")
    assert [(loc.name, loc.remote) for loc in prof.search.locations] == [("Worldwide", True)]


def test_short_or_unreadable_postings_fail_clearly(monkeypatch, capsys):
    with pytest.raises(profile_draft.DraftError, match="too short"):
        profile_draft.draft(profile_draft.Posting(description="hi"))
    monkeypatch.setattr(profile_draft, "fetch", lambda url: (_ for _ in ()).throw(profile_draft.DraftError("login")))
    assert profile.main(["from-url", "https://www.linkedin.com/jobs/view/123456789/"]) == 1
    assert json.loads(capsys.readouterr().out)["error"] == "login"


@pytest.mark.parametrize(
    "url, job_id",
    [
        ("https://www.linkedin.com/jobs/view/4300123456/", "4300123456"),
        ("https://www.linkedin.com/jobs/view/senior-data-analyst-at-acme-4300123456?trk=x", "4300123456"),
        ("https://www.linkedin.com/jobs/collections/recommended/?currentJobId=4300123456", "4300123456"),
    ],
)
def test_linkedin_links_are_read_through_the_public_page(monkeypatch, url, job_id):
    from jobradar.sources import linkedin_public

    seen = []
    monkeypatch.setattr(
        linkedin_public,
        "detail_page",
        lambda jid: (
            seen.append(jid)
            or {"title": "Data Analyst", "companyName": "Acme", "location": "Remote", "description": "x" * 300}
        ),
    )
    posting = profile_draft.fetch(url)
    assert seen == [job_id] and posting.title == "Data Analyst" and posting.url == url
