"""Dates: a single tolerant reader for what the sources bring."""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any


def parse_datetime(value: Any) -> datetime | None:
    """ISO 8601 (with ``Z`` or without timezone), date only, or epoch in seconds or milliseconds.

    Returns a timezone-aware datetime (UTC if the value had none), or ``None`` if not understood.
    """
    if value is None or isinstance(value, bool):
        return None
    if isinstance(value, datetime):
        dt = value
    elif isinstance(value, (int, float)):
        seconds = value / 1000 if value > 1e11 else value
        return datetime.fromtimestamp(seconds, tz=UTC)
    else:
        text = str(value).strip()
        if not text:
            return None
        try:
            dt = datetime.fromisoformat(text.replace("Z", "+00:00"))
        except ValueError:
            return None
    return dt if dt.tzinfo else dt.replace(tzinfo=UTC)
