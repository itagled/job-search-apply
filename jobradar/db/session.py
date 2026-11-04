"""SQLAlchemy engine and sessions, created on first use.

Only ``DATABASE_URL`` is needed (via ``jobradar.config``).
"""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, TypeVar

from sqlalchemy import create_engine, make_url
from sqlalchemy.dialects import postgresql, sqlite
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.exc import OperationalError
from sqlalchemy.orm import Session, sessionmaker

from jobradar import config

T = TypeVar("T")

_ENGINE: Engine | None = None
_SessionLocal: sessionmaker[Session] | None = None


def get_database_url() -> str:
    """``DATABASE_URL`` from the environment; ``ConfigError`` (a ``RuntimeError``) if missing.

    A relative SQLite file (``sqlite:///data/jobradar.db``) lives under the project root, whatever the
    current folder, and its folder is created if needed.
    """
    raw = config.require("DATABASE_URL")
    url = make_url(raw)
    if url.get_backend_name() != "sqlite" or url.database in (None, "", ":memory:"):
        return raw
    path = Path(url.database)
    if not path.is_absolute():
        path = config.ROOT / path
    path.parent.mkdir(parents=True, exist_ok=True)
    return url.set(database=str(path)).render_as_string(hide_password=False)


def get_engine() -> Engine:
    """Return the cached engine, creating it on first use."""
    global _ENGINE
    if _ENGINE is None:
        _ENGINE = create_engine(get_database_url(), pool_pre_ping=True)
    return _ENGINE


def _get_session_factory() -> sessionmaker[Session]:
    """Return the cached sessionmaker bound to the engine."""
    global _SessionLocal
    if _SessionLocal is None:
        _SessionLocal = sessionmaker(bind=get_engine(), autoflush=False, autocommit=False)
    return _SessionLocal


class _LazySessionFactory:
    """Callable proxy so that ``SessionLocal()`` works like ``sessionmaker()``."""

    def __call__(self, *args: Any, **kwargs: Any) -> Session:
        return _get_session_factory()(*args, **kwargs)


SessionLocal = _LazySessionFactory()


def get_session() -> Session:
    """Create a new session bound to the default engine."""
    return SessionLocal()


def insert_for(bind: Session | Connection | str) -> Callable[..., Any]:
    """The ``insert`` with ``on_conflict_do_nothing``/``on_conflict_do_update`` of the database in use.

    ``bind`` is a session, a connection or the dialect name (``postgresql`` or ``sqlite``).
    """
    if isinstance(bind, Session):
        bind = bind.get_bind()
    name = bind if isinstance(bind, str) else bind.dialect.name
    if name == "postgresql":
        return postgresql.insert
    if name == "sqlite":
        return sqlite.insert
    raise NotImplementedError(f"unsupported database {name!r}: use Postgres or SQLite")


def write_with_retry(fn: Callable[[Session], T], *, attempts: int = 3, pause: float = 2.0) -> T:
    """Run ``fn(session)`` in a new session and commit.

    If the connection drops (``OperationalError``), discard the pool and retry the whole block,
    up to ``attempts`` times: ``fn`` must be repeatable (upserts, ``COALESCE``). Useful for
    batch writes after calling an API, without keeping the connection open in the meantime.
    """
    attempt = 1
    while True:
        session = get_session()
        try:
            result = fn(session)
            session.commit()
            return result
        except OperationalError:
            session.rollback()
            get_engine().dispose()  # the next attempt opens new connections
            if attempt >= attempts:
                raise
            attempt += 1
            time.sleep(pause)
        finally:
            session.close()
