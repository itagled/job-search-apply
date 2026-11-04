from __future__ import annotations

import json
from datetime import UTC, datetime

import pytest
from sqlalchemy import create_engine
from sqlalchemy.orm import Session, sessionmaker

from jobradar import config, http_client
from jobradar.db.models import Base, JobPost
from jobradar.sources.base import REGISTRY
from jobradar.sources.connectors import load_all
from tests.conftest import fake_response

TODAY = datetime.now(UTC).isoformat()


@pytest.fixture
def fake_network(monkeypatch):
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


def test_registry_has_the_productive_sources():
    load_all()
    expected = {
        "remote_remotive",
        "latam_getonbrd",
        "remote_himalayas",
        "ats_ashby",
        "ats_greenhouse",
        "ats_lever",
        "ats_workable",
        "web_clay_community",
        "portal_cargo",
        "linkedin_jobs_guest",
        "linkedin_jobs_valig",
        "linkedin_job_alert_email",
    }
    assert expected <= set(REGISTRY)
    assert REGISTRY["linkedin_jobs_valig"].paid and not REGISTRY["linkedin_job_alert_email"].applies_filters


def test_boards_from_db_and_file(monkeypatch, fake_network):
    from jobradar.sources.connectors import ats_boards

    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        s.add(
            JobPost(
                source="ats_greenhouse",
                external_id="1",
                status="new",
                source_metadata={"ats_greenhouse": {"board": "acme", "ats": "greenhouse"}},
            )
        )
        s.commit()
    monkeypatch.setattr("jobradar.db.session.SessionLocal", sessionmaker(bind=engine))
    extra = config.DATA_DIR / "sources" / "ats_boards.txt"
    extra.parent.mkdir(parents=True)
    extra.write_text("greenhouse:beta\nlever:otra\n", encoding="utf-8")
    connector = REGISTRY["ats_greenhouse"]()
    assert connector.boards() == ["acme", "beta"]
    fake_network["https://boards-api.greenhouse.io/v1/boards/acme/jobs?content=true"] = {
        "jobs": [
            {
                "id": 7,
                "title": "RevOps Manager",
                "location": {"name": "Remote"},
                "absolute_url": "https://x/7",
                "content": "x",
                "first_published": TODAY,
                "offices": [],
            },
            {
                "id": 8,
                "title": "Backend Engineer",
                "location": {"name": "Remote"},
                "absolute_url": "https://x/8",
                "content": "x",
                "first_published": TODAY,
                "offices": [],
            },
        ]
    }
    assert [a.id for a in connector.search(days=7)] == ["7"]
    assert isinstance(ats_boards._BoardsAts, type)


def test_cargo_extracts_embedded_postings():
    from jobradar.sources.connectors.cargo import _id, _in_reach, parse

    jobs = [
        {
            "jobTitle": "GTM Engineer",
            "jobUrl": "https://jobs.lever.co/acme/abc",
            "companyName": "Acme",
            "jobLocation": "remote",
            "country": "United States",
        }
    ]
    compact = json.dumps({"x": {"jobs": jobs}}, separators=(",", ":"))  # Next.js leaves it without spaces
    payload = json.dumps(compact)[1:-1]  # and escaped as a JS string
    page = f'<script>self.__next_f.push([1,"{payload}"])</script>'
    assert parse(page) == jobs
    assert _in_reach(jobs[0]) and _id(jobs[0]["jobUrl"]) == "lever:abc"
    assert _id("https://example.com/careers/1?utm=x").startswith("cargo:")
    assert not _in_reach(
        {"jobTitle": "RevOps", "jobLocation": "onSite", "country": "Germany", "jobUrl": "https://example.de/1"}
    )


def test_clay_community_builds_posting_with_only_publisher():
    from jobradar.sources.connectors.clay_community import _post, _posting

    ld = {
        "@type": "DiscussionForumPosting",
        "datePublished": "2026-10-20T10:00:00Z",
        "headline": "GTM Engineer @ Acme",
        "text": "Company: Acme\nLocation: Remote (LATAM)\nApply: https://jobs.ashbyhq.com/acme/1",
        "author": {"name": "Ana", "url": "https://community.clay.com/u/ana"},
        "comment": [{"author": {"name": "Otra persona"}}],
    }
    post = _post(f'<script type="application/ld+json">{json.dumps(ld)}</script>')
    assert set(post) == {"date", "title", "text", "author", "author_url"}
    posting = _posting("https://community.clay.com/x/share-jobs/msg_1/gtm", "msg_1", "share-jobs", post)
    assert (posting.id, posting.company, posting.location) == ("clay_community:msg_1", "Acme", "Remote (LATAM)")
    assert posting.apply_url == "https://jobs.ashbyhq.com/acme/1" and posting.extra["poster"] == "Ana"


@pytest.fixture
def fake_apify(monkeypatch):
    """Mocked ``apify.run``: returns the items loaded into it and records each call."""
    from jobradar import apify

    calls, items = [], []

    def run(actor, payload, **kw):
        calls.append({"actor": actor, "payload": payload, **kw})
        return apify.ApifyRun(items=list(items), run_id="r1", status="SUCCEEDED", cost_usd=0.01)

    monkeypatch.setattr(apify, "run", run)
    return calls, items


def test_glassdoor_maps_and_sets_spend_cap(fake_apify):
    calls, items = fake_apify
    items.append(
        {
            "id": 9,
            "title": "RevOps Manager",
            "employer": {"name": "Acme"},
            "location": {"name": "Austin, TX"},
            "seoUrl": "https://glassdoor/9",
            "description": "<p>Hola</p>",
            "ageInDays": 2,
        }
    )
    postings = list(REGISTRY["glassdoor"]().search(days=7))
    assert postings[0].location == "Austin, TX (Remote)" and postings[0].description == "Hola"
    assert all(c["max_total_charge_usd"] > 0 and c["payload"]["daysOld"] == 7 for c in calls)


def test_wellfound_discards_old(fake_apify):
    _, items = fake_apify
    items += [
        {"jobId": 1, "jobTitle": "GTM Engineer", "remote": True, "postedAt": TODAY},
        {"jobId": 2, "jobTitle": "GTM Engineer", "remote": True, "postedAt": "2025-01-01"},
    ]
    postings = list(REGISTRY["wellfound"]().search(days=7))
    assert {a.id for a in postings} == {"wellfound:1"} and postings[0].location == "Remote (anywhere)"


def test_hiring_posts(monkeypatch, fake_apify):
    from jobradar import llm
    from jobradar.sources import linkedin_public
    from jobradar.sources.connectors import hiring_posts as pc

    _, items = fake_apify
    items += [
        {
            "id": "111",
            "content": "Hiring a GTM Engineer! Apply: https://www.linkedin.com/jobs/view/4400000001/",
            "author": {"name": "Ana"},
            "reactions": [{"name": "otra persona"}],
        },
        {"id": "222", "content": "Buscamos RevOps Lead remoto LATAM", "author": {"name": "Luis"}},
        {"id": "333", "content": "Tips para tu CV"},
    ]
    decisions = {
        "111": {"is_job_offer": True, "is_relevant": True, "eligible": True, "role_title": "GTM Engineer"},
        "222": {
            "is_job_offer": True,
            "is_relevant": True,
            "eligible": None,
            "role_title": "RevOps Lead",
            "remote": True,
        },
        "333": {"is_job_offer": False},
    }

    def decide(system, user, **kw):
        key = "111" if "Hiring" in user else "222" if "Buscamos" in user else "333"
        return llm.LLMResponse(decisions[key])

    monkeypatch.setattr(llm, "chat_json", decide)
    monkeypatch.setattr(
        linkedin_public,
        "detail_page",
        lambda jid: {"title": "GTM Engineer", "description": "Ficha", "companyName": "Acme"},
    )
    postings = {a.id: a for a in pc.HiringPosts().search(days=7)}
    assert set(postings) == {"4400000001", "urn:li:activity:222"}
    assert postings["4400000001"].description == "Ficha" and postings["urn:li:activity:222"].location == "Remote"


def test_paid_ones_do_not_run_without_permission(monkeypatch, capsys):
    from jobradar.sources import __main__ as cli

    class Session:
        def close(self):
            pass

    monkeypatch.setenv("NANOGPT_API_KEY", "test-key")
    monkeypatch.setattr(cli, "_session", Session)
    monkeypatch.setattr(cli.Deduplicator, "from_db", classmethod(lambda cls, s: cls()))
    assert cli.main(["run", "glassdoor"]) == 0
    assert "--paid" in json.loads(capsys.readouterr().out)["skipped"]
