"""Serper client (paid Google Search via API): a search and its raw response."""

from __future__ import annotations

import logging
from typing import Any

from jobradar import config, http_client, raw

log = logging.getLogger(__name__)

URL = "https://google.serper.dev/search"


def configured() -> bool:
    return bool(config.get("SERPER_API_KEY"))


def search(query: str, *, gl: str = "", hl: str = "", timeout: float = 20) -> dict[str, Any] | None:
    """Google results for ``query`` (knowledge graph + organic), or ``None`` on failure."""
    key = config.get("SERPER_API_KEY")
    if not key:
        return None
    body = {k: v for k, v in {"q": query, "gl": gl, "hl": hl}.items() if v}
    resp = http_client.post(
        URL,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        json=body,
        timeout=timeout,
        retries=2,
        retry_status=frozenset({429}),
    )
    if resp.status_code != 200:
        log.warning("Serper HTTP %s for %r", resp.status_code, query)
        return None
    try:
        data = resp.json()
    except ValueError:
        return None
    raw.append("serper", [data], query=body)
    return data
