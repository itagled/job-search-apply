"""Base declarative and types shared by all models (they work on Postgres and on SQLite)."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from sqlalchemy import JSON, BigInteger, DateTime, Integer
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.engine import Dialect
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.types import TypeDecorator

# JSONB in Postgres and generic JSON in other engines (SQLite).
JSONType = JSON().with_variant(JSONB(), "postgresql")
# BigInteger autoincrements in Postgres; SQLite only autoincrements INTEGER.
PKType = BigInteger().with_variant(Integer(), "sqlite")


def one_of(column: str, values: tuple[str, ...]) -> str:
    """SQL condition of a ``CHECK``: ``column`` takes one of ``values``."""
    return f"{column} IN (" + ", ".join(f"'{v}'" for v in values) + ")"


class TZDateTime(TypeDecorator):
    """A timezone-aware timestamp on every database.

    Postgres stores ``timestamptz``. SQLite has no time zones: values are stored in UTC and come
    back with ``tzinfo=UTC``, so comparing or subtracting them with ``datetime.now(UTC)`` works the
    same on both. Naive datetimes are taken as UTC.
    """

    impl = DateTime(timezone=True)
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Dialect) -> Any:
        if isinstance(value, datetime) and dialect.name == "sqlite":
            value = (value if value.tzinfo else value.replace(tzinfo=UTC)).astimezone(UTC).replace(tzinfo=None)
        return value

    def process_result_value(self, value: Any, dialect: Dialect) -> Any:
        if isinstance(value, datetime) and value.tzinfo is None:
            value = value.replace(tzinfo=UTC)
        return value


class Base(DeclarativeBase):
    pass
