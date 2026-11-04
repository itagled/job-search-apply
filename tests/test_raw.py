from __future__ import annotations

import json
from datetime import date

from jobradar import raw


def test_append_saves_each_record_with_context(monkeypatch, tmp_path):
    monkeypatch.setattr(raw, "RAW_DIR", tmp_path)
    path = raw.append("apify", [{"id": 1}, {"id": 2}], actor="a/b", params={"q": "x"})
    lines = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert [line["data"]["id"] for line in lines] == [1, 2]
    assert all(line["actor"] == "a/b" and line["saved_at"] for line in lines)
    assert raw.append("apify", []).exists()  # an empty batch does not break or add lines


def test_purge_deletes_only_expired_third_party(monkeypatch, tmp_path):
    monkeypatch.setattr(raw, "RAW_DIR", tmp_path / "raw")
    monkeypatch.setattr(raw, "THIRD_PARTY_DIR", tmp_path / "raw" / "third_party")
    old = raw.THIRD_PARTY_DIR / "posts" / "2025-01-01.jsonl"
    new = raw.THIRD_PARTY_DIR / "posts" / "2026-10-01.jsonl"
    own = raw.RAW_DIR / "llm" / "2025-01-01.jsonl"
    for path in (old, new, own):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("{}\n", encoding="utf-8")
    assert raw.purge(365, today=date(2026, 10, 28)) == [old]
    assert new.exists() and own.exists() and not old.exists()
