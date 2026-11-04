"""Shared HTTP: per-thread session, timeouts, retries, per-domain pause and disk cache.

Replaces the loose ``requests.get/post``. ``request`` returns the final response even if
it is not 2xx: each client decides what to do with the status. It retries on network failures and
on 429/5xx (respecting ``Retry-After``); never on 4xx authentication or balance errors, which
are not fixed by waiting.

It is not called ``http.py`` on purpose: it would shadow the standard library's ``http``.
"""

from __future__ import annotations

import hashlib
import json
import logging
import os
import random
import tempfile
import threading
import time
from collections.abc import Callable
from dataclasses import dataclass
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from urllib.parse import urlsplit

import requests

log = logging.getLogger(__name__)

DEFAULT_TIMEOUT = 30.0
RETRY_STATUS = frozenset({429, 500, 502, 503, 504})
MAX_RETRY_AFTER = 120.0
USER_AGENT = "Mozilla/5.0 (compatible; jobradar/0.1)"

_local = threading.local()
_next_slot: dict[str, float] = {}
_throttle_lock = threading.Lock()


def session() -> requests.Session:
    """A ``requests.Session`` of its own per thread (scorers and scrapers use threads)."""
    s = getattr(_local, "session", None)
    if s is None:
        s = requests.Session()
        s.headers["User-Agent"] = USER_AGENT
        _local.session = s
    return s


def throttle(url: str, min_interval: float) -> None:
    """Wait as needed to avoid hitting the same domain more than once every ``min_interval`` s."""
    host = urlsplit(url).hostname or ""
    with _throttle_lock:
        now = time.monotonic()
        slot = max(now, _next_slot.get(host, 0.0))
        _next_slot[host] = slot + min_interval
    if slot > now:
        time.sleep(slot - now)


def _safe_url(url: str) -> str:
    """URL without querystring, for logs (tokens usually go there)."""
    return urlsplit(url)._replace(query="", fragment="").geturl()


def _retry_after(resp: requests.Response) -> float | None:
    value = resp.headers.get("Retry-After")
    if not value:
        return None
    try:
        seconds = float(value)
    except ValueError:
        try:
            seconds = parsedate_to_datetime(value).timestamp() - time.time()
        except (TypeError, ValueError):
            return None
    return max(0.0, min(seconds, MAX_RETRY_AFTER))


def request(
    method: str,
    url: str,
    *,
    retries: int = 3,
    backoff: float = 1.0,
    retry_status: frozenset[int] = RETRY_STATUS,
    retry_if: Callable[[requests.Response], bool] | None = None,
    min_interval: float = 0.0,
    timeout: float = DEFAULT_TIMEOUT,
    **kwargs: Any,
) -> requests.Response:
    """Make the request with retries and increasing wait (with jitter).

    ``retry_if`` replaces ``retry_status`` when the rule depends on the body.
    ``min_interval`` enables the per-domain pause. Raises ``requests.RequestException``
    only if the network fails on all attempts.
    """
    attempt = 0
    while True:
        if min_interval:
            throttle(url, min_interval)
        try:
            resp = session().request(method, url, timeout=timeout, **kwargs)
        except (requests.ConnectionError, requests.Timeout) as exc:
            if attempt >= retries:
                raise
            delay = backoff * 2**attempt + random.uniform(0, backoff)
            log.warning(
                "%s %s failed (%s); retry %d/%d in %.1fs",
                method,
                _safe_url(url),
                type(exc).__name__,
                attempt + 1,
                retries,
                delay,
            )
        else:
            retry = retry_if(resp) if retry_if else resp.status_code in retry_status
            if not retry or attempt >= retries:
                return resp
            delay = _retry_after(resp) or backoff * 2**attempt + random.uniform(0, backoff)
            log.warning(
                "%s %s → HTTP %s; retry %d/%d in %.1fs",
                method,
                _safe_url(url),
                resp.status_code,
                attempt + 1,
                retries,
                delay,
            )
        time.sleep(delay)
        attempt += 1


def get(url: str, **kwargs: Any) -> requests.Response:
    return request("GET", url, **kwargs)


def post(url: str, **kwargs: Any) -> requests.Response:
    return request("POST", url, **kwargs)


class DiskCache:
    """Disk cache, one JSON file per key, with optional expiration (``ttl`` in seconds)."""

    def __init__(self, folder: Path, ttl: float | None = None) -> None:
        self.folder = Path(folder)
        self.ttl = ttl

    @staticmethod
    def key(*parts: Any) -> str:
        raw = json.dumps(parts, sort_keys=True, ensure_ascii=False, default=str)
        return hashlib.sha256(raw.encode("utf-8")).hexdigest()

    def _path(self, key: str) -> Path:
        return self.folder / key[:2] / f"{key}.json"

    def get(self, key: str) -> Any:
        path = self._path(key)
        try:
            if self.ttl is not None and time.time() - path.stat().st_mtime > self.ttl:
                return None
            return json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return None

    def set(self, key: str, value: Any) -> None:
        path = self._path(key)
        path.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=path.parent, suffix=".tmp")
        with os.fdopen(fd, "w", encoding="utf-8") as fh:
            json.dump(value, fh, ensure_ascii=False)
        os.replace(tmp, path)


@dataclass
class CachedResponse:
    status_code: int
    text: str
    url: str
    from_cache: bool = False

    @property
    def ok(self) -> bool:
        return 200 <= self.status_code < 300

    def json(self) -> Any:
        return json.loads(self.text)


def cached_get(url: str, *, cache: DiskCache, params: dict | None = None, **kwargs: Any) -> CachedResponse:
    """GET that stores 200 responses in ``cache`` (others are not stored, to retry later)."""
    key = DiskCache.key(url, params or {})
    hit = cache.get(key)
    if hit is not None:
        return CachedResponse(hit["status_code"], hit["text"], hit["url"], from_cache=True)
    resp = get(url, params=params, **kwargs)
    out = CachedResponse(resp.status_code, resp.text, resp.url)
    if resp.status_code == 200:
        cache.set(key, {"status_code": out.status_code, "text": out.text, "url": out.url})
    return out
