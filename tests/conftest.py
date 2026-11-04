"""Tests without network or real DB: fake keys, the repo's .env not loaded, and data/ in a temporary folder."""

from __future__ import annotations

import pytest
import requests


@pytest.fixture(autouse=True)
def _isolated_env(monkeypatch, tmp_path):
    """Each test sees a clean environment: the real .env is not loaded and nothing is written to data/."""
    from jobradar import config, llm, raw
    from jobradar.sources import ats

    monkeypatch.setattr(config, "_loaded", True)
    for name in list(config.VARIABLES):
        monkeypatch.delenv(name, raising=False)
    # the public example profile (GTM Engineer living in Chile), never the user's own
    monkeypatch.setenv("PROFILE_PATH", str(config.ROOT / "profile" / "examples" / "gtm_engineer_latam.md"))
    monkeypatch.setattr(config, "DATA_DIR", tmp_path / "data")
    monkeypatch.setattr(raw, "RAW_DIR", tmp_path / "data" / "raw")
    monkeypatch.setattr(raw, "THIRD_PARTY_DIR", tmp_path / "data" / "raw" / "third_party")
    monkeypatch.setattr(llm, "_cache", None)
    monkeypatch.setattr(llm, "_usage", {})
    monkeypatch.setattr(ats, "_cache", None)


@pytest.fixture(autouse=True)
def _titles_by_keyword(request, monkeypatch):
    """Without network a title is the role if it contains a keyword; tests/test_titles.py checks the real call."""
    from jobradar.sources import titles

    monkeypatch.setattr(titles, "_answers", {})
    if request.module.__name__.endswith("test_titles"):
        return

    def ask(prof, batch):
        words = [k.lower() for k in prof.search.keywords]
        return {title: 0.9 if any(w in title.lower() for w in words) else 0.02 for title in batch}

    monkeypatch.setattr(titles, "_ask", ask)


@pytest.fixture
def sqlite_db(monkeypatch, tmp_path):
    """A new SQLite database with every table, the one ``SessionLocal`` and ``get_engine`` hand out."""
    from sqlalchemy import create_engine
    from sqlalchemy.orm import sessionmaker

    from jobradar.db import session as db
    from jobradar.db.models import Base

    engine = create_engine(f"sqlite:///{tmp_path / 'jobradar.db'}")
    Base.metadata.create_all(engine)
    monkeypatch.setattr(db, "_ENGINE", engine)
    monkeypatch.setattr(db, "_SessionLocal", sessionmaker(bind=engine, autoflush=False))
    yield engine
    engine.dispose()


def fake_response(
    status: int = 200,
    payload=None,
    *,
    text: str | None = None,
    headers: dict | None = None,
    url: str = "https://example.com/",
) -> requests.Response:
    """A hand-built ``requests.Response`` (JSON or text)."""
    import json

    r = requests.Response()
    r.status_code = status
    r._content = (text if text is not None else json.dumps(payload)).encode("utf-8")
    r.headers.update(headers or {})
    r.url = url
    return r
