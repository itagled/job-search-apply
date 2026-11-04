from __future__ import annotations

import json
from datetime import UTC, datetime, timedelta

import pytest
from sqlalchemy import create_engine, select
from sqlalchemy.orm import Session

from jobradar import http_client
from jobradar.db.models import Base, JobPost
from jobradar.sources import ats, filters
from jobradar.sources.base import Posting
from jobradar.sources.dedupe import Deduplicator, canon_url, same_company
from jobradar.sources.loader import load
from tests.conftest import fake_response

NOW = datetime(2026, 10, 28, tzinfo=UTC)


def _posting(id_="1", title="GTM Engineer", **kw) -> Posting:
    kw.setdefault("location", "Remote - LATAM")
    kw.setdefault("posted_at", NOW - timedelta(days=2))
    return Posting(id=id_, title=title, **kw)


# --- filters ------------------------------------------------------------------------------


def test_eligibility_from_chile():
    assert filters.eligible("RevOps", "Santiago, Chile", "")[0]
    assert filters.eligible("RevOps", "Remote - Worldwide", "")[0]
    assert not filters.eligible("RevOps", "London, UK (hybrid)", "")[0]
    ok, why = filters.eligible("RevOps", "Remote - United States", "You must reside in the United States.")
    assert not ok and why.startswith("foreign_location")


def test_accept_discards_old_postings():
    assert filters.accept(_posting(), now=NOW)[0]
    ok, why = filters.accept(_posting(posted_at=NOW - timedelta(days=60)), now=NOW)
    assert not ok and why.startswith("old")


# --- deduplication ------------------------------------------------------------------------


def test_canonical_url_keeps_id_and_removes_tracking():
    assert canon_url("https://www.Indeed.com/viewjob?jk=abc&utm_source=x#top") == "indeed.com/viewjob?jk=abc"
    assert canon_url("https://jobs.lever.co/acme/123/?lever-source=li") == "jobs.lever.co/acme/123"
    assert same_company("acme", "acme inc") and not same_company("ab", "ab corp")


def test_deduplicator():
    class Row:
        def __init__(self, id, source, external_id, post_url, company, title):
            self.id, self.source, self.external_id = id, source, external_id
            self.post_url, self.company, self.title = post_url, company, title

    d = Deduplicator([Row(1, "linkedin_jobs_guest", "9", "https://jobs.lever.co/acme/123", "Acme Inc", "GTM Engineer")])
    assert d.duplicate("ats_lever", _posting(url="https://jobs.lever.co/acme/123?ref=x")) == (1, "linkedin_jobs_guest")
    assert d.duplicate("remote_remotive", _posting(company="Acme")) == (1, "linkedin_jobs_guest")
    assert d.duplicate("linkedin_jobs_guest", _posting(id_="9")) is None  # same source: it is updated
    assert d.duplicate("remote_remotive", _posting(company="Otra")) is None


# --- loader -----------------------------------------------------------------------------


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def test_load_filters_deduplicates_and_saves(session):
    postings = [
        _posting("1", description="Build the outbound engine with Clay and HubSpot.", company="Acme"),
        _posting("2", title="SDR"),  # does not pass the title
        _posting("3", title="RevOps Lead", company="Beta", url="https://beta.example/jobs/3"),
        _posting("1", title="GTM Engineer"),  # repeated
    ]
    res = load("remote_remotive", postings, session=session, now=NOW)
    assert (res.seen, res.accepted, res.new, res.with_description) == (4, 2, 2, 1)
    assert res.discarded["title:title_not_target"] == 1 and res.discarded["duplicate"] == 1
    row = session.scalar(select(JobPost).where(JobPost.external_id == "1"))
    assert row.description_fetch_status == "done" and row.source_metadata["remote_remotive"]["company"] == "Acme"
    # Another source with the same notice (same URL) is not duplicated; the same source is updated.
    other = load(
        "linkedin_jobs_guest",
        [_posting("77", title="RevOps Lead", url="https://beta.example/jobs/3")],
        session=session,
        now=NOW,
    )
    same = load("remote_remotive", [_posting("3", title="RevOps Lead", company="Beta Corp")], session=session, now=NOW)
    assert (other.duplicates_other_source, other.new) == (1, 0)
    assert (same.new, same.updated) == (0, 1)


def test_dry_run_does_not_write(session):
    res = load("remote_remotive", [_posting()], session=session, dry_run=True, now=NOW)
    assert res.new == 1 and session.scalar(select(JobPost.id)) is None


# --- ATS ----------------------------------------------------------------------------------


def test_parse_url():
    assert ats.parse_url("https://jobs.ashbyhq.com/acme/abc-123") == ("ashby", "acme", "abc-123")
    assert ats.parse_url("https://boards.greenhouse.io/acme/jobs/42?gh_src=x") == ("greenhouse", "acme", "42")
    assert ats.parse_url("https://job-boards.greenhouse.io/embed/job_app?for=acme&token=7") == (
        "greenhouse",
        "acme",
        "7",
    )
    assert ats.parse_url("https://apply.workable.com/acme/j/AB12/") == ("workable", "acme", "AB12")
    assert ats.parse_url("https://example.com/careers/1") is None


@pytest.fixture
def fake_network(monkeypatch):
    """Simulates the network: ``urls`` maps URL → response; everything else responds 404."""
    urls: dict[str, object] = {}

    def fake(method, url, **kw):
        found = urls.get(url)
        if found is None:
            return fake_response(404, {})
        return found if not isinstance(found, (dict, list)) else fake_response(200, found)

    monkeypatch.setattr(http_client, "request", fake)
    monkeypatch.setattr(http_client, "get", lambda url, **kw: fake("GET", url, **kw))
    monkeypatch.setattr(http_client, "post", lambda url, **kw: fake("POST", url, **kw))
    return urls


def test_list_greenhouse_and_closed_detail(fake_network):
    fake_network["https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"] = {
        "jobs": [
            {
                "id": 42,
                "title": "GTM Engineer",
                "location": {"name": "Remote - LATAM"},
                "absolute_url": "https://x/42",
                "content": "&lt;p&gt;Construir&lt;/p&gt;",
                "first_published": "2026-10-20T10:00:00Z",
                "offices": [],
            }
        ]
    }
    fake_network["https://boards-api.greenhouse.io/v1/boards/acme"] = {"name": "Acme"}
    [posting] = ats.list_board("greenhouse", "acme")
    assert (posting.id, posting.company, posting.description, posting.remote) == ("42", "Acme", "Construir", True)
    assert ats.detail("https://boards.greenhouse.io/acme/jobs/99").closed


def test_fill_in_from_page_jsonld(fake_network):
    job = {
        "@type": "JobPosting",
        "title": "RevOps",
        "description": "<p>Texto completo</p>",
        "datePosted": "2026-10-25",
        "jobLocationType": "TELECOMMUTE",
        "hiringOrganization": {"name": "Acme"},
    }
    fake_network["https://acme.example/jobs/1"] = fake_response(
        200, text=f'<script type="application/ld+json">{json.dumps(job)}</script>'
    )
    posting = _posting(url="https://acme.example/jobs/1", posted_at=None)
    assert ats.fill_in(posting, read_page=True)
    assert posting.description == "Texto completo" and posting.remote and posting.posted_at.day == 25


# --- connectors ---------------------------------------------------------------------------


def test_getonbrd_portal_rule():
    from jobradar.sources.connectors.getonbrd import _posting as getonbrd

    base = {
        "title": "RevOps",
        "published_at": 1761500000,
        "description": "<p>Hola</p>",
        "company": {"data": {"attributes": {"name": "Acme"}}},
    }
    remote = getonbrd("x", {"attributes": {**base, "remote_modality": "fully_remote"}})
    assert remote.location == "Remote" and remote.company == "Acme" and remote.description.endswith("Hola")
    outside = {**base, "remote_modality": "no_remote", "countries": ["Peru"]}
    assert getonbrd("y", {"attributes": outside}) is None


def test_himalayas_stops_when_reaching_old_postings(fake_network):
    from jobradar.sources.connectors.remote_boards import Himalayas

    new = {
        "guid": "https://himalayas.app/companies/acme/jobs/gtm",
        "title": "GTM Engineer",
        "companyName": "Acme",
        "pubDate": int(datetime.now(UTC).timestamp()),
        "description": "<p>x</p>",
    }
    old = {**new, "guid": "https://himalayas.app/companies/acme/jobs/old", "pubDate": 1500000000}
    fake_network["https://himalayas.app/jobs/api/search?q=gtm%20engineer&country=CL&sort=recent&page=1"] = {
        "jobs": [new, old]
    }
    postings = list(Himalayas().search(days=7))
    assert [a.id for a in postings] == ["gtm"] and postings[0].location == "Worldwide (remote)"


def test_cli_list_and_manual_load(monkeypatch, tmp_path, capsys, session):
    from jobradar.sources import __main__ as cli

    assert cli.main(["list"]) == 0
    output = capsys.readouterr().out
    assert "remote_remotive" in output and "latam_getonbrd" in output
    file = tmp_path / "postings.json"
    file.write_text(
        json.dumps(
            [
                {
                    "id": "a1",
                    "title": "GTM Engineer",
                    "location": "Remote LATAM",
                    "posted_at": datetime.now(UTC).isoformat(),
                    "portal": "manual",
                }
            ]
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(cli, "_session", lambda: session)
    assert cli.main(["load", "--source", "manual", str(file)]) == 0
    assert json.loads(capsys.readouterr().out)["new"] == 1


def test_duplicate_from_other_source_fills_description(session):
    url = "https://www.linkedin.com/jobs/view/555/"
    load(
        "linkedin_job_alert_email",
        [_posting("555", title="Any title", url=url)],
        session=session,
        now=NOW,
        apply_filters=False,
    )
    res = load(
        "linkedin_jobs_guest", [_posting("555", url=url, description="Full description")], session=session, now=NOW
    )
    assert (res.duplicates_other_source, res.new, res.with_description) == (1, 0, 1)
    row = session.scalar(select(JobPost).where(JobPost.source == "linkedin_job_alert_email"))
    assert row.job_description_full == "Full description" and row.title == "Any title"


def test_linkedin_public_opens_only_new_detail_pages(monkeypatch):
    from jobradar.sources import linkedin_public
    from jobradar.sources.connectors import linkedin

    cards = [
        {"id": "1", "title": "GTM Engineer", "companyName": "Acme", "postedDate": "2026-10-27"},
        {"id": "2", "title": "Revenue Operations Lead", "companyName": "Beta"},
        {"id": "3", "title": "Account Executive"},
    ]
    requested = []
    monkeypatch.setattr(linkedin_public, "search_cards", lambda *a, **k: cards)
    monkeypatch.setattr(
        linkedin_public, "detail_page", lambda jid: requested.append(jid) or {"description": f"desc {jid}"}
    )
    monkeypatch.setattr(linkedin, "_with_description", lambda ids: {"2"})
    postings = {a.id: a for a in linkedin.LinkedInPublic().search(days=7)}
    assert set(postings) == {"1", "2"} and requested == ["1"]
    assert postings["1"].description == "desc 1" and postings["2"].description is None


def test_a_source_without_google_credentials_is_skipped(monkeypatch, capsys, session):
    from jobradar.sources import __main__ as cli

    monkeypatch.setenv("NANOGPT_API_KEY", "test-key")
    monkeypatch.setattr(cli, "_session", lambda: session)
    assert cli.main(["run", "linkedin_job_alert_email", "--dry-run"]) == 0
    out = json.loads(capsys.readouterr().out)
    assert out == {"source": "linkedin_job_alert_email", "skipped": "no Google credentials"}


def test_location_rules_only_apply_with_a_location_preset(monkeypatch, tmp_path):
    from jobradar.sources.connectors.cargo import _in_reach

    onsite = {"jobLocation": "onSite", "country": "Germany", "jobUrl": "https://example.de/1"}
    assert filters.out_of_reach_on_site("Madrid, Spain") and not filters.out_of_reach_on_site("Santiago, Chile")
    assert not _in_reach(onsite)
    path = tmp_path / "target.md"
    path.write_text("+++\nrole = 'Analyst'\n[search]\nkeywords = ['analyst']\n+++\nSQL.", encoding="utf-8")
    monkeypatch.setenv("PROFILE_PATH", str(path))
    assert not filters.out_of_reach_on_site("Madrid, Spain") and _in_reach(onsite)


def test_job_boards_of_one_field_only_run_if_the_profile_lists_them():
    from jobradar.sources import __main__ as cli
    from jobradar.sources.connectors import load_all

    load_all()
    assert "remote_remotive" in cli._free_sources([]) and "portal_cargo" not in cli._free_sources([])
    assert "web_clay_community" not in cli._free_sources(["portal_cargo"])
    assert "portal_cargo" in cli._free_sources(["portal_cargo"])


def test_running_sources_without_a_profile_says_what_to_do(monkeypatch, tmp_path, caplog):
    from jobradar.sources import __main__ as cli

    monkeypatch.setenv("PROFILE_PATH", str(tmp_path / "missing.md"))
    assert cli.main(["run", "--free", "--dry-run"]) == 1
    assert "jobradar profile from-url" in caplog.text


def test_running_sources_without_the_key_says_where_to_get_it(caplog):
    from jobradar.sources import __main__ as cli

    assert cli.main(["run", "--free", "--dry-run"]) == 1
    assert "https://nano-gpt.com/api" in caplog.text and "https://openrouter.ai/settings/keys" in caplog.text


def test_running_out_of_balance_stops_every_source(monkeypatch, caplog, session):
    from jobradar import llm
    from jobradar.sources import __main__ as cli
    from jobradar.sources.base import REGISTRY
    from jobradar.sources.connectors import load_all

    load_all()
    runs = []

    def out_of_balance(self, *, days):
        runs.append(self.name)
        raise llm.OutOfCreditsError("top up")

    monkeypatch.setenv("NANOGPT_API_KEY", "test-key")
    monkeypatch.setattr(cli, "_session", lambda: session)
    for name in ("remote_himalayas", "remote_remotive"):
        monkeypatch.setattr(REGISTRY[name], "search", out_of_balance)
    assert cli.main(["run", "remote_himalayas", "remote_remotive", "--dry-run"]) == 1
    assert runs == ["remote_himalayas"] and "top up" in caplog.text
