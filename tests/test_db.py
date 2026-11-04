from __future__ import annotations

from datetime import UTC, datetime

import pytest
from alembic import command
from alembic.autogenerate import compare_metadata
from alembic.config import Config
from alembic.migration import MigrationContext
from sqlalchemy import create_engine, inspect, select, text
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from jobradar import config
from jobradar.db.models import Base, JobPost
from jobradar.db.upsert import NormalizedPost, UpsertStats, bulk_upsert_job_posts, ingest_job_post

ROOT = config.ROOT


def _alembic(conn) -> Config:
    cfg = Config(str(ROOT / "alembic.ini"))
    cfg.set_main_option("script_location", str(ROOT / "migrations"))
    cfg.attributes["connection"] = conn
    return cfg


@pytest.mark.filterwarnings("ignore:autogenerate skipping")  # SQLite does not reflect expression indexes
def test_migrations_up_match_models_and_down(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        command.upgrade(_alembic(conn), "head")
    with engine.connect() as conn:
        mc = MigrationContext.configure(
            conn,
            opts={
                "compare_type": True,
                "include_name": lambda name, type_, parents: type_ != "table" or name in Base.metadata.tables,
            },
        )
        assert compare_metadata(mc, Base.metadata) == []
    with engine.begin() as conn:
        command.downgrade(_alembic(conn), "base")
    assert set(inspect(engine).get_table_names()) <= {"alembic_version"}


def test_migrations_keep_the_rules_of_the_models(tmp_path):
    engine = create_engine(f"sqlite:///{tmp_path / 'm.db'}")
    with engine.begin() as conn:
        command.upgrade(_alembic(conn), "head")
    company = (
        "INSERT INTO target_company (name, domain, stage, sources, source_metadata, heuristic_signals) "
        "VALUES ('Acme', :domain, :stage, '[]', '{}', '{}')"
    )
    with engine.begin() as conn:
        conn.execute(text(company), {"domain": "acme.example", "stage": "raw"})
    for values in ({"domain": "other.example", "stage": "nope"}, {"domain": "ACME.example", "stage": "raw"}):
        with pytest.raises(IntegrityError), engine.begin() as conn:
            conn.execute(text(company), values)


@pytest.fixture
def session():
    engine = create_engine("sqlite://")
    Base.metadata.create_all(engine)
    with Session(engine) as s:
        yield s


def _post(ext: str, **kw) -> NormalizedPost:
    return NormalizedPost(external_id=ext, post_url=f"https://example.com/{ext}", **kw)


def test_ingest_job_post_is_idempotent_and_does_not_resurrect(session):
    stats = UpsertStats()
    first_id, inserted = ingest_job_post(session, source="s", post=_post("1", title="GTM"), stats=stats)
    session.get(JobPost, first_id).status = "discarded"
    again_id, inserted_again = ingest_job_post(session, source="s", post=_post("1", company="Acme"), stats=stats)
    row = session.get(JobPost, again_id)
    assert (first_id, inserted, inserted_again) == (again_id, True, False)
    assert (row.title, row.company, row.status) == ("GTM", "Acme", "discarded")
    assert stats.as_dict() == {"inserted": 1, "updated": 1, "skipped": 0}


def test_bulk_load_fills_gaps_without_overwriting_and_merges_metadata(session):
    stats = bulk_upsert_job_posts(
        session,
        source="s",
        posts=[
            _post("1", title="GTM Engineer", source_metadata={"a": 1}),
            _post("2", title="RevOps"),
            _post("2", title="RevOps Lead"),  # repeated in the batch: the last one wins
        ],
    )
    session.commit()
    assert stats.as_dict() == {"inserted": 2, "updated": 0, "skipped": 0}
    session.execute(JobPost.__table__.update().where(JobPost.external_id == "1").values(status="applied"))
    stats = bulk_upsert_job_posts(
        session,
        source="s",
        posts=[
            _post("1", title=None, company="Acme", source_metadata={"b": 2}),
        ],
    )
    session.commit()
    row = session.scalar(select(JobPost).where(JobPost.external_id == "1"))
    session.refresh(row)
    assert stats.as_dict() == {"inserted": 0, "updated": 1, "skipped": 0}
    assert (row.title, row.company, row.status) == ("GTM Engineer", "Acme", "applied")
    assert row.source_metadata == {"a": 1, "b": 2}
    assert session.scalar(select(JobPost.title).where(JobPost.external_id == "2")) == "RevOps Lead"


def test_bulk_load_compiles_for_postgres():
    from sqlalchemy.dialects import postgresql

    from jobradar.db.upsert import _bulk_upsert_statement

    sql = str(
        _bulk_upsert_statement(
            "postgresql", [{"source": "s", "external_id": "1", "status": "new", "source_metadata": {}}]
        ).compile(dialect=postgresql.dialect())
    )
    assert "ON CONFLICT (source, external_id) DO UPDATE" in sql
    assert "job_post.source_metadata || excluded.source_metadata" in sql
    assert "coalesce(excluded.title, job_post.title)" in sql and "status" not in sql.split("DO UPDATE")[1]


def test_insert_for_picks_the_insert_of_each_database(session):
    from sqlalchemy.dialects import postgresql, sqlite

    from jobradar.db.session import insert_for

    assert insert_for("postgresql") is postgresql.insert and insert_for(session) is sqlite.insert
    with pytest.raises(NotImplementedError):
        insert_for("mysql")


def test_a_relative_sqlite_file_lives_under_the_project(monkeypatch, tmp_path):
    from sqlalchemy import make_url

    from jobradar.db.session import get_database_url

    monkeypatch.setattr(config, "ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_URL", "sqlite:///data/x.db")
    assert make_url(get_database_url()).database == str(tmp_path / "data" / "x.db")
    assert (tmp_path / "data").is_dir()
    for url in ("sqlite://", "sqlite:///:memory:", "postgresql://user:p%40ss@host:5432/db"):
        monkeypatch.setenv("DATABASE_URL", url)
        assert get_database_url() == url


def test_set_description():
    row = JobPost(source="s", external_id="1")
    when = datetime(2026, 10, 1, tzinfo=UTC)
    assert not row.set_description("   ")
    assert row.set_description("Full description", when=when)
    assert (row.description_fetch_status, row.description_fetched_at) == ("done", when)
    assert not row.set_description("Another")
    assert row.set_description("Another", overwrite=True) and row.job_description_full == "Another"


def test_timestamps_come_back_with_a_time_zone_on_sqlite(session):
    from datetime import timedelta, timezone

    chile = timezone(timedelta(hours=-3))
    session.add_all(
        [
            JobPost(source="s", external_id="naive", posted_at=datetime(2026, 10, 1, 12, 0)),
            JobPost(source="s", external_id="aware", posted_at=datetime(2026, 10, 1, 9, 0, tzinfo=chile)),
        ]
    )
    session.commit()
    rows = {p.external_id: p for p in session.scalars(select(JobPost))}
    assert rows["naive"].posted_at == rows["aware"].posted_at == datetime(2026, 10, 1, 12, 0, tzinfo=UTC)
    assert rows["naive"].created_at.tzinfo is UTC  # server default (CURRENT_TIMESTAMP), read back as UTC
    assert datetime.now(UTC) - rows["naive"].created_at < timedelta(minutes=5)
