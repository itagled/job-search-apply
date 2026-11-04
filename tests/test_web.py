from __future__ import annotations

from datetime import UTC, datetime

from jobradar.web import status
from jobradar.web.texts import TEXTS

DATA_FIELDS = {"now", "summary", "up", "rec", "tasks", "rep", "npend", "pending", "total", "chips", "trs"}


def test_status_page_template_has_every_placeholder():
    found = {m.group("braced") for m in status.PAGE.pattern.finditer(status.PAGE.template) if m.group("braced")}
    template_texts = {k for k in TEXTS["en"] if k.startswith("t_")}
    assert found == DATA_FIELDS | template_texts | {"lang"}
    page = status.PAGE.substitute({name: f"<{name}>" for name in found})
    assert page.startswith("<!doctype html>") and "<trs>" in page and "${" not in page


def test_every_language_has_the_same_texts():
    en, es = TEXTS["en"], TEXTS["es"]
    assert set(en) == set(es) and set(en["stages"]) == set(es["stages"]) == set(status.STAGES)
    assert len(en["weekdays"]) == len(es["weekdays"]) == 7 and len(en["months"]) == len(es["months"]) == 12


def test_dates_follow_the_ui_language(monkeypatch):
    when = datetime(2026, 8, 3, 12, 0, tzinfo=UTC)  # Monday in every time zone from UTC-11 to UTC+11
    assert status.date(when) == f"mon 3-aug · {when.astimezone():%H:%M}"  # in local time
    monkeypatch.setenv("UI_LANGUAGE", "es")
    assert status.date(when, False) == "lun 3-ago"
