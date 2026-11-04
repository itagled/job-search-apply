from __future__ import annotations

from datetime import UTC, datetime

from jobradar.companies.normalize import normalize_name
from jobradar.dates import parse_datetime
from jobradar.db.upsert import UpsertStats
from jobradar.text import host, normalize, strip_accents


def test_text():
    assert strip_accents("  Árbol de Decisión ") == "arbol de decision"
    assert normalize("José-María  O'Higgins!") == "jose maria o higgins"
    assert host("https://www.Acme.example/jobs?x=1") == "acme.example" and host("acme.cl/x") == "acme.cl"
    assert host(None) == ""
    assert normalize_name("Acme SpA") == "acme"


def test_dates():
    utc = UTC
    assert parse_datetime("2026-10-01T12:00:00Z") == datetime(2026, 10, 1, 12, tzinfo=utc)
    assert parse_datetime("2026-10-01") == datetime(2026, 10, 1, tzinfo=utc)
    assert parse_datetime(1759320000) == parse_datetime(1759320000000) == datetime(2025, 10, 1, 12, tzinfo=utc)
    assert parse_datetime(datetime(2026, 1, 1)) == datetime(2026, 1, 1, tzinfo=utc)
    assert parse_datetime("ayer") is None and parse_datetime("") is None and parse_datetime(True) is None


def test_upsert_stats_unique():
    stats = UpsertStats(inserted=2, skipped=1)
    assert stats.as_dict() == {"inserted": 2, "updated": 0, "skipped": 1}
