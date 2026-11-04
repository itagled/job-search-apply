from __future__ import annotations

import json

import pytest

from jobradar import config, profile
from jobradar.sources import filters
from jobradar.sources.base import Posting

MINIMAL = """+++
role = "Data Analyst"
[search]
keywords = ["data analyst", "analytics engineer"]
+++
I want to analyze product data with SQL and Python.
"""


def test_a_minimal_profile_gets_generic_defaults():
    prof = profile.parse(MINIMAL)
    assert prof.role == "Data Analyst" and prof.description.startswith("I want")
    assert prof.location.preset == "any" and prof.search.max_age_days == 45
    assert {d.key for d in prof.scoring.dimensions} >= set(prof.scoring.identity)
    assert prof.search.for_source("getonbrd") == ["data analyst", "analytics engineer"]
    assert prof.outreach_exclude is None


@pytest.mark.parametrize(
    "text, message",
    [
        ("+++\nrole = 'x'\n+++\n", "no description"),
        ("+++\n[search]\nkeywords = []\n+++\ntext", "add .search. keywords"),
        ("+++\n[search]\nkeywords = ['a']\n[outreach]\nexclude_titles = '('\n+++\ntext", "invalid regex"),
        ("+++\n[search]\nkeywords = ['a']\n[location]\npreset = 'latam'\n+++\ntext", "needs home"),
        ("+++\n[search]\nkeywords = ['a']\n[scoring]\nidentity = {nope = 1}\n+++\ntext", "identity"),
        ("+++\nrole = 'x'\ntext", "never closed"),
    ],
)
def test_invalid_profiles_explain_what_is_wrong(text, message):
    with pytest.raises(profile.ProfileError, match=message):
        profile.parse(text)


def test_a_missing_profile_says_how_to_create_one(tmp_path, monkeypatch, capsys):
    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "target.md"))
    with pytest.raises(profile.ProfileError, match="from-url"):
        profile.load()
    assert profile.main(["check"]) == 1 and not json.loads(capsys.readouterr().out)["ok"]


def test_the_example_profile_is_valid(capsys):
    assert profile.main(["check"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out["role"] == "GTM Engineer" and out["location"]["preset"] == "latam"
    assert out["scoring"]["identity"] == {"gtm_build_focus": 0.6, "gtm_domain_context": 0.4}


def test_the_template_is_valid():
    prof = profile.load(config.ROOT / "profile" / "target.example.md")
    assert prof.role == "Data Analyst" and prof.location.preset == "any"


def test_filters_follow_a_generic_profile(tmp_path, monkeypatch):
    target = tmp_path / "target.md"
    target.write_text(MINIMAL, encoding="utf-8")
    monkeypatch.setenv("PROFILE_PATH", str(target))
    assert filters.accept(Posting(id="1", title="Data Analyst", location="Madrid, Spain")) == (True, "any_location")
    assert filters.accept(Posting(id="2", title="Registered Nurse")) == (False, "title:title_not_target")
    assert not filters.home_matches("Madrid") and filters.hires_in_region("hiring in LATAM") is None
