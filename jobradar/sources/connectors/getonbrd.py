"""Get on Board (public API v0): all categories and keyword searches."""

from __future__ import annotations

import json
from collections.abc import Iterable, Iterator
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from jobradar import http_client, profile
from jobradar.dates import parse_datetime
from jobradar.sources import filters
from jobradar.sources.base import Connector, Posting, register
from jobradar.text import strip_html

API = "https://www.getonbrd.com/api/v0"
EXPAND = quote(json.dumps(["company", "location_tenants", "location_cities", "location_regions"]).replace(" ", ""))


@register
class GetOnBoard(Connector):
    name = "latam_getonbrd"
    description = "Get on Board: public API, every category plus keyword searches"

    def _pages(self, path: str) -> Iterator[dict[str, Any]]:
        page = 1
        while True:
            sep = "&" if "?" in path else "?"
            resp = http_client.get(
                f"{API}{path}{sep}per_page=100&page={page}&expand={EXPAND}", min_interval=0.5, timeout=40
            )
            data = resp.json() if resp.ok else {}
            self.save_raw(data, path=path, page=page)
            if not data.get("data"):
                return
            yield from data["data"]
            if page >= ((data.get("meta") or {}).get("total_pages") or 1):
                return
            page += 1

    def search(self, *, days: int) -> Iterable[Posting]:
        limit = datetime.now(UTC) - timedelta(days=days)
        categories = http_client.get(f"{API}/categories?per_page=100", min_interval=0.5, timeout=40)
        raw: dict[str, dict] = {}
        for cat in (categories.json() if categories.ok else {}).get("data", []):
            for job in self._pages(f"/categories/{cat['id']}/jobs"):
                raw.setdefault(job["id"], job)
        for query in profile.load().search.for_source("getonbrd"):
            for job in self._pages(f"/search/jobs?query={quote(query)}"):
                raw.setdefault(job["id"], job)
        for jid, job in raw.items():
            posting = _posting(jid, job)
            if posting and (posting.posted_at is None or posting.posted_at >= limit):
                yield posting


def _names(attrs: dict, key: str) -> list[dict]:
    return [t.get("attributes", {}) for t in (attrs.get(key) or {}).get("data", [])]


def _posting(jid: str, job: dict) -> Posting | None:
    """API notice; ``None`` if on-site or hybrid away from home (portal-specific rule)."""
    a = job.get("attributes") or {}
    parts = [
        ((a.get(h) or "") + "\n" + strip_html(a[k])).strip()
        for h, k in (
            ("description_headline", "description"),
            ("functions_headline", "functions"),
            ("desirable_headline", "desirable"),
            ("benefits_headline", "benefits"),
        )
        if a.get(k)
    ]
    if a.get("projects"):
        parts.append("About the company / project\n" + strip_html(a["projects"]))
    tenants = [t["name"] for t in _names(a, "location_tenants") if t.get("name")]
    cities = [f"{t.get('name')}, {t.get('country')}" for t in _names(a, "location_cities")]
    countries = a.get("countries") or []
    work_mode = a.get("remote_modality")
    remote = work_mode in ("fully_remote", "remote_local")
    if not remote and filters.out_of_reach_on_site("; ".join([*countries, *tenants, *cities])):
        return None
    if remote:
        location = (
            ("Remote / " + "; ".join(cities))
            if cities
            else ("Remote" + (f" ({', '.join(tenants)})" if tenants else ""))
        )
    else:
        location = "; ".join(cities or countries)
    company = (a.get("company") or {}).get("data") or {}
    url = (job.get("links") or {}).get("public_url") or f"https://www.getonbrd.com/jobs/{jid}"
    salary = (
        f"USD {a.get('min_salary') or '?'}-{a.get('max_salary') or '?'} /month (gross)"
        if a.get("min_salary") or a.get("max_salary")
        else None
    )
    return Posting(
        id=str(jid),
        title=a.get("title") or "",
        company=(company.get("attributes") or {}).get("name") or company.get("id"),
        location=location,
        url=url,
        apply_url=url,
        description="\n\n".join(parts),
        posted_at=parse_datetime(a.get("published_at")),
        remote=remote,
        salary=salary,
        extra={
            "remote_modality": work_mode,
            "countries_names": countries,
            "location_tenants": tenants,
            "location_cities": cities,
            "category": a.get("category_name"),
            "getonbrd": job,
        },
    )
