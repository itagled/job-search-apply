"""The title filter: what is asked to Jev, what is kept and what happens when it fails."""

from __future__ import annotations

import json

import pytest

from jobradar import config, llm
from jobradar.sources import filters, titles
from jobradar.sources.base import REGISTRY, Posting
from jobradar.sources.connectors import ats_boards


@pytest.fixture
def jev(monkeypatch):
    """Fake Jev: ``scores`` maps a word of the question to its answer. Returns the calls it got."""
    calls: list[dict] = []
    scores = {"GTM": 0.8, "Borderline": 0.2, "Nurse": 0.05}

    def systemone(state, questions, *, model, cache=True):
        calls.append({"state": state, "questions": questions, "model": model, "cache": cache})
        return {
            "answers": {
                key: {"type": "noul", "noul": next((v for w, v in scores.items() if w in q["instructions"]), 0.01)}
                for key, q in questions.items()
            }
        }

    monkeypatch.setattr(titles.llm, "systemone", systemone)
    return calls


def test_jev_gets_the_role_and_one_question_per_title(jev):
    titles.judge(["GTM Engineer"])
    [call] = jev
    assert call["model"] == "typesafe/jev-latest" and call["cache"] is False
    assert call["state"]["target_role"] == "GTM Engineer" and "RevOps" in call["state"]["also_searched_as"]
    question = call["questions"]["t0"]
    assert question["type"] == "noul" and '"GTM Engineer"' in question["instructions"]


def test_the_cut_decides_and_can_be_changed(jev, monkeypatch):
    assert filters.title_in_family("GTM Engineer") == (True, "title_match")
    assert filters.title_in_family("Borderline Title") == (True, "title_match")
    assert filters.title_in_family("Registered Nurse") == (False, "title_not_target")
    monkeypatch.setenv("TITLE_MIN", "0.5")
    assert not filters.title_in_family("Borderline Title")[0]


@pytest.mark.parametrize("title", [None, "", "   "])
def test_an_empty_title_is_not_asked(jev, title):
    assert filters.title_in_family(title) == (False, "no_title")
    titles.judge([title])
    assert jev == []


def test_titles_go_in_batches_without_repeating(jev):
    many = [f"GTM Engineer {i}" for i in range(titles.BATCH + 1)]
    titles.judge([*many, "gtm   engineer 0", None, ""])
    assert [len(call["questions"]) for call in jev] == [titles.BATCH, 1]


def test_an_answer_is_never_paid_twice(jev, monkeypatch):
    titles.judge(["GTM Engineer"])
    assert filters.title_in_family("gtm engineer")[0]
    monkeypatch.setattr(titles, "_answers", {})  # a new run reads what was kept
    assert filters.title_in_family("GTM Engineer")[0]
    assert len(jev) == 1


def test_changing_the_role_asks_again(jev, monkeypatch, tmp_path):
    titles.judge(["GTM Engineer"])
    target = tmp_path / "target.md"
    target.write_text("+++\nrole = 'Nurse'\n[search]\nkeywords = ['nurse']\n+++\nCaring.", encoding="utf-8")
    monkeypatch.setenv("PROFILE_PATH", str(target))
    titles.judge(["GTM Engineer"])
    assert [call["state"]["target_role"] for call in jev] == ["GTM Engineer", "Nurse"]


def test_a_failed_call_raises_and_keeps_what_arrived(jev, monkeypatch):
    real = titles.llm.systemone

    def fails_on_the_second(*args, **kwargs):
        if jev:
            raise llm.LLMError("NanoGPT HTTP 500")
        return real(*args, **kwargs)

    monkeypatch.setattr(titles.llm, "systemone", fails_on_the_second)
    many = [f"GTM Engineer {i}" for i in range(titles.BATCH + 1)]
    with pytest.raises(llm.LLMError):
        titles.judge(many)
    [file] = (config.DATA_DIR / "cache" / "titles").glob("*.jsonl")
    assert len(file.read_text(encoding="utf-8").splitlines()) == titles.BATCH


def test_an_incomplete_answer_is_an_error(monkeypatch):
    monkeypatch.setattr(titles.llm, "systemone", lambda *a, **k: {"answers": {}})
    with pytest.raises(llm.LLMError, match="every title"):
        titles.judge(["GTM Engineer"])


def test_a_run_cut_off_while_saving_does_not_break_the_next_one(jev, monkeypatch):
    titles.judge(["GTM Engineer"])
    [file] = (config.DATA_DIR / "cache" / "titles").glob("*.jsonl")
    with file.open("a", encoding="utf-8") as out:
        out.write('{"title": "half a li')
    monkeypatch.setattr(titles, "_answers", {})
    assert filters.title_in_family("GTM Engineer")[0] and len(jev) == 1
    assert json.loads(file.read_text(encoding="utf-8").splitlines()[0])["score"] == 0.8


def test_without_jev_an_llm_picks_the_titles_to_keep(jev, monkeypatch):
    calls: list[dict] = []

    def chat_json(system, user, **kwargs):
        calls.append({"system": system, "user": user, **kwargs})
        return llm.LLMResponse({"keep": [0, "2", "x"]})

    monkeypatch.setattr(titles.llm, "chat_json", chat_json)
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    titles.judge(["GTM Engineer", "Registered Nurse", "RevOps Lead"])
    [call] = calls
    assert call["model"] == "deepseek/deepseek-v4-flash-0731" and call["temperature"] == 0 and call["cache"] is False
    assert "role: GTM Engineer" in call["system"] and call["user"].splitlines()[1] == "1. Registered Nurse"
    assert [filters.title_in_family(t)[0] for t in ("GTM Engineer", "Registered Nurse", "RevOps Lead")] == [
        True,
        False,
        True,
    ]
    assert jev == [] and len(list((config.DATA_DIR / "cache" / "titles").glob("*.jsonl"))) == 1
    monkeypatch.delenv("OPENROUTER_API_KEY")
    monkeypatch.setenv("NANOGPT_API_KEY", "nano-key")
    titles.judge(["GTM Engineer"])  # another judge keeps its own answers
    assert len(jev) == 1


def test_without_jev_an_answer_without_a_list_is_an_error(monkeypatch):
    monkeypatch.setenv("OPENROUTER_API_KEY", "or-key")
    monkeypatch.setattr(titles.llm, "chat_json", lambda *a, **k: llm.LLMResponse({"keep": "all of them"}))
    with pytest.raises(llm.LLMError, match="list to keep"):
        titles.judge(["GTM Engineer"])


def test_a_board_is_judged_in_one_call(jev, monkeypatch):
    connector = REGISTRY["ats_lever"]()
    monkeypatch.setattr(connector, "boards", lambda: ["a", "b"])
    postings = {
        "a": [Posting(id="1", title="GTM Engineer", description="x")],
        "b": [Posting(id="2", title="Registered Nurse", description="x")],
    }
    monkeypatch.setattr(ats_boards.ats, "list_board", lambda ats, board: postings[board])
    assert [p.id for p in connector.search(days=7)] == ["1"]
    assert len(jev) == 1 and len(jev[0]["questions"]) == 2
