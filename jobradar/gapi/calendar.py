"""Google Calendar: read events (``google`` profile)."""

from __future__ import annotations

from typing import Any

from jobradar.gapi import auth


def service() -> Any:
    return auth.service("calendar", "v3", "google")


def list_events(
    *,
    time_min: str,
    time_max: str,
    query: str | None = None,
    max_results: int = 250,
    calendar_id: str = "primary",
    svc: Any = None,
) -> list[dict[str, Any]]:
    """Events between ``time_min`` and ``time_max`` (RFC3339), with recurring ones expanded."""
    svc = svc or service()
    events: list[dict[str, Any]] = []
    page_token: str | None = None
    while len(events) < max_results:
        kwargs: dict[str, Any] = {
            "calendarId": calendar_id,
            "timeMin": time_min,
            "timeMax": time_max,
            "singleEvents": True,
            "orderBy": "startTime",
            "maxResults": min(2500, max_results - len(events)),
        }
        if query:
            kwargs["q"] = query
        if page_token:
            kwargs["pageToken"] = page_token
        res = auth.execute(svc.events().list(**kwargs))
        events += res.get("items", []) or []
        page_token = res.get("nextPageToken")
        if not page_token:
            break
    return events
