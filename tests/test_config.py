from __future__ import annotations

import re

import pytest

from jobradar import config

ROOT = config.ROOT
CODE_DIRS = ("jobradar", "companies", "cv", "db", "fit", "outreach", "scripts", "sync", "util", "web")


def _code_files():
    for folder in CODE_DIRS:
        yield from (ROOT / folder).rglob("*.py") if (ROOT / folder).is_dir() else ()
    yield from ROOT.glob("*.py")


def test_get_uses_default_and_env_wins(monkeypatch):
    assert config.get("FIT_TRIAGE") == "jev"
    monkeypatch.setenv("FIT_TRIAGE", "none")
    assert config.get("FIT_TRIAGE") == "none"


def test_require_explains_what_is_missing():
    with pytest.raises(config.ConfigError, match="DATABASE_URL"):
        config.require("DATABASE_URL")


def test_unregistered_variable_is_error():
    with pytest.raises(KeyError, match="DOES_NOT_EXIST"):
        config.get("DOES_NOT_EXIST")


def test_types(monkeypatch):
    monkeypatch.setenv("FIT_TRIAGE_MIN", "30.5")
    assert config.get_float("FIT_TRIAGE_MIN") == 30.5
    monkeypatch.setenv("APIFY_EMPLOYEES_LOCATIONS", " Chile, Peru ,")
    assert config.get_list("APIFY_EMPLOYEES_LOCATIONS") == ["Chile", "Peru"]
    monkeypatch.setenv("LLM_CACHE", "0")
    assert config.get_bool("LLM_CACHE") is False
    monkeypatch.setenv("FIT_TRIAGE_MIN", "a lot")
    with pytest.raises(config.ConfigError):
        config.get_float("FIT_TRIAGE_MIN")


def test_code_only_asks_for_registered_variables():
    requested = set()
    for path in _code_files():
        text = path.read_text(encoding="utf-8")
        requested |= set(re.findall(r"config\.(?:get|require|get_float|get_bool|get_list)\(\s*\"([A-Z0-9_]+)\"", text))
    assert requested, "no uses of config found"
    assert requested <= set(config.VARIABLES), requested - set(config.VARIABLES)


def test_no_one_else_reads_env():
    culprits = set()
    for path in _code_files():
        rel = path.relative_to(ROOT).as_posix()
        if rel == "jobradar/config.py":
            continue
        if re.search(r"os\.(getenv|environ)|load_dotenv", path.read_text(encoding="utf-8")):
            culprits.add(rel)
    assert not culprits, f"leen el entorno sin pasar por jobradar.config: {sorted(culprits)}"


def test_env_example_is_up_to_date():
    actual = (ROOT / ".env.example").read_text(encoding="utf-8")
    assert actual == config.env_example(), "run: jobradar config --env-example"
