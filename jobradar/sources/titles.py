"""Is a job title the role of the profile? Jev answers, before a posting is opened.

Job boards list thousands of titles, and no list of words covers every way of naming a role, least of
all in several languages. So each title is a yes/no question to Jev (TypeSafe System One via NanoGPT, see
``jobradar/fit/jev.py``): the state is the role and its keywords, and one call carries ``BATCH`` titles.
Titles at ``TITLE_MIN`` or above pass. The fit score decides later with the full posting, so the cut is
low: a title let through costs little, a title lost is never seen again. Where there is no Jev
(OpenRouter), a cheap LLM (``TITLE_MODEL``) picks the titles to keep from the same batches.

Each answer stays in ``data/cache/titles/<profile>.jsonl`` as soon as it arrives, so no title is paid
twice; changing the role, the keywords or the model starts a new file. A failed call raises: that source
is skipped in this run, and no title is lost in silence.

Benchmark 2026-11-01 (2,000 real titles of an accounting and a UX profile, in English and Spanish): of
the titles on which they disagreed, Jev lost none that were clearly the role, the cheap LLM with this
prompt lost 23 for the same price, and regex rules written from the same posting lost 189 that both
models kept. Cost ~US$0.0016 per 1,000 titles (~38 tokens each), about 5 s.
"""

from __future__ import annotations

import hashlib
import json
from collections.abc import Iterable
from pathlib import Path

from jobradar import config, llm, profile

BATCH = 200
MAX_CHARS = 200
QUESTION = 'A job titled "{}" is the target role or a close variant of it (the same kind of work)'
LLM_PROMPT = """You filter job titles for someone searching for this role: {role}.
It is also searched as: {keywords}.

You get a numbered list of job titles, in any language. Return the numbers of the titles that could be this role or a close variant: the same kind of work, in any language and at any seniority. A different profession stays out even if it shares a word (an "Account Executive" is sales, not accounting). When unsure, keep the title.

Answer ONLY with JSON: {{"keep": [numbers]}}"""

_answers: dict[Path, dict[str, float]] = {}  # answers file -> title -> probability that it is the role


def _clean(title: str) -> str:
    return " ".join(title.split())[:MAX_CHARS]


def _key(title: str) -> str:
    return _clean(title).casefold()


def _judge() -> str:
    """Who answers: Jev on NanoGPT, the title model elsewhere."""
    return (config.get("JEV_MODEL") or "") if llm.has_jev() else f"llm:{config.get('TITLE_MODEL')}"


def _file(prof: profile.Profile) -> Path:
    asked = json.dumps([prof.role, prof.search.keywords, _judge()], ensure_ascii=False)
    return config.DATA_DIR / "cache" / "titles" / f"{hashlib.sha256(asked.encode()).hexdigest()[:16]}.jsonl"


def _known(file: Path) -> dict[str, float]:
    if file not in _answers:
        _answers[file] = {}
        for line in file.read_text(encoding="utf-8").splitlines() if file.is_file() else []:
            try:
                row = json.loads(line)
            except ValueError:  # the last line of a run that was cut off
                continue
            _answers[file][row["title"]] = row["score"]
    return _answers[file]


def _ask(prof: profile.Profile, titles: list[str]) -> dict[str, float]:
    """Probability that each title is the role."""
    return _ask_jev(prof, titles) if llm.has_jev() else _ask_llm(prof, titles)


def _ask_llm(prof: profile.Profile, titles: list[str]) -> dict[str, float]:
    """Without Jev: the LLM keeps some numbers of the list (1.0) and leaves out the rest (0.0)."""
    system = LLM_PROMPT.format(role=prof.role, keywords=", ".join(prof.search.keywords))
    user = "\n".join(f"{i}. {title}" for i, title in enumerate(titles))
    data = llm.chat_json(system, user, model=config.get("TITLE_MODEL") or "", temperature=0, cache=False).data
    if not isinstance(data.get("keep"), list):
        raise llm.LLMError(f"the title check did not return a list to keep: {str(data)[:150]}")
    kept = set()
    for number in data["keep"]:
        try:
            kept.add(int(number))
        except (TypeError, ValueError):
            continue
    return {title: 1.0 if i in kept else 0.0 for i, title in enumerate(titles)}


def _ask_jev(prof: profile.Profile, titles: list[str]) -> dict[str, float]:
    """Jev's probability that each title is the role."""
    state = {"target_role": prof.role, "also_searched_as": ", ".join(prof.search.keywords)}
    questions = {f"t{i}": {"type": "noul", "instructions": QUESTION.format(t)} for i, t in enumerate(titles)}
    data = llm.systemone(state, questions, model=config.get("JEV_MODEL") or "", cache=False)
    try:
        return {t: float(data["answers"][f"t{i}"]["noul"]) for i, t in enumerate(titles)}
    except (KeyError, TypeError, ValueError) as exc:
        raise llm.LLMError(f"Jev did not answer every title: {exc!r}") from None


def judge(titles: Iterable[str | None]) -> None:
    """Ask for the titles without an answer yet: one call per batch, much cheaper than one per title."""
    prof = profile.load()
    file = _file(prof)
    known = _known(file)
    new = list({_key(t): _clean(t) for t in titles if t and _key(t) and _key(t) not in known}.values())
    for i in range(0, len(new), BATCH):
        scores = {_key(title): score for title, score in _ask(prof, new[i : i + BATCH]).items()}
        file.parent.mkdir(parents=True, exist_ok=True)
        rows = (json.dumps({"title": t, "score": s}, ensure_ascii=False) + "\n" for t, s in scores.items())
        with file.open("a", encoding="utf-8") as out:
            out.write("".join(rows))
        known.update(scores)


def score(title: str) -> float:
    """Probability that ``title`` is the role of the profile."""
    judge([title])
    return _known(_file(profile.load()))[_key(title)]
