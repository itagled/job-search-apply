"""Remote work job boards with a free public API: Remotive and Himalayas.

Torre was left out: its search API changed in October 2026 (returns 400).
"""

from __future__ import annotations

import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any
from urllib.parse import quote

from jobradar import http_client, profile
from jobradar.dates import parse_datetime
from jobradar.sources import ats, filters, titles
from jobradar.sources.base import Connector, Posting, register
from jobradar.text import strip_html


def _limit(days: int) -> datetime:
    return datetime.now(UTC) - timedelta(days=days)


def _remotive_title(hit: dict[str, Any]) -> str:
    return re.sub(r"\s+", " ", strip_html(hit.get("title"))).strip()


@register
class Remotive(Connector):
    """Remotive's internal search: the "hirable from <home>" facet, completed with keyword searches."""

    name = "remote_remotive"
    description = "Remotive: the home-country facet plus keyword searches (live_jobs index)"
    URL = "https://remotive.com/api/v2/jobs/search/"
    HEADERS = {"Referer": "https://remotive.com/remote-jobs", "Origin": "https://remotive.com"}
    MAX_FACET_PAGES = 160  # ~8,000 listings; it cuts off earlier, when the page is already older than the window

    def _search(self, params: str) -> dict[str, Any]:
        resp = http_client.post(
            self.URL,
            headers=self.HEADERS,
            min_interval=0.35,
            timeout=40,
            json={"requests": [{"indexName": "live_jobs", "params": params}], "paywallProfile": "custom"},
        )
        data = resp.json() if resp.ok else {}
        self.save_raw(data, params=params)
        return ((data.get("result") or {}).get("results") or data.get("results") or [{}])[0]

    def search(self, *, days: int) -> Iterable[Posting]:
        limit = _limit(days)
        seen: dict[str, dict] = {}
        prof = profile.load()
        home = prof.location.home_name
        facet = quote(json.dumps([f"location_filters:{home}"]))
        for page in range(self.MAX_FACET_PAGES if home else 0):  # sorted from newest to oldest
            res = self._search(f"query=&hitsPerPage=50&page={page}&facetFilters={facet}")
            recent = [h for h in res.get("hits") or [] if (parse_datetime(h.get("discovered_on")) or limit) >= limit]
            seen.update({str(h["id"]): h for h in recent})
            if not recent or page + 1 >= int(res.get("nbPages") or 0):
                break
        for keyword in prof.search.for_source("remote_boards"):
            for page in range(2):
                res = self._search(f"query={quote(keyword)}&hitsPerPage=50&page={page}")
                for h in res.get("hits") or []:
                    if (parse_datetime(h.get("discovered_on")) or limit) >= limit:
                        seen.setdefault(str(h["id"]), h)
                if page + 1 >= int(res.get("nbPages") or 1):
                    break
        titles.judge(_remotive_title(h) for h in seen.values())
        for hid, h in seen.items():
            title = _remotive_title(h)
            if not filters.title_in_family(title)[0]:
                continue
            page_url = h.get("remotive_com_url") or h.get("url")
            job = (
                ats.jsonld_jobposting(http_client.get(page_url, min_interval=0.4, timeout=40).text)
                if page_url
                else None
            )
            job = job or {}
            external = h.get("url") if h.get("url") and "remotive.com" not in h["url"] else None
            salary = h.get("salary") if h.get("salary") not in (None, "", "unspecified") else None
            yield Posting(
                id=hid,
                title=title,
                company=h.get("company_name"),
                location=", ".join(strip_html(x) for x in h.get("locations") or []),
                url=page_url,
                apply_url=external or page_url,
                description=strip_html(job.get("description")),
                posted_at=parse_datetime(job.get("datePosted") or h.get("discovered_on")),
                remote=True,
                salary=salary,
                extra={"remotive": {k: v for k, v in h.items() if not k.startswith("_")}, "ats_url": external},
            )


@register
class Himalayas(Connector):
    """Himalayas search API filtered by the home country (includes listings without restriction)."""

    name = "remote_himalayas"
    description = "Himalayas: search API filtered by the home country, by keyword"

    def search(self, *, days: int) -> Iterable[Posting]:
        limit, seen = _limit(days), set()
        prof = profile.load()
        country = f"&country={min(prof.location.home_codes)}" if prof.location.home_codes else ""
        for keyword in prof.search.for_source("remote_boards"):
            for page in range(1, 8):
                url = f"https://himalayas.app/jobs/api/search?q={quote(keyword)}{country}&sort=recent&page={page}"
                resp = http_client.get(url, min_interval=0.8, timeout=40)
                jobs = (resp.json() if resp.ok else {}).get("jobs") or []
                self.save_raw(jobs, query=keyword, page=page)
                titles.judge(j.get("title") for j in jobs)
                old = False
                for j in jobs:
                    gid = j.get("guid") or j.get("applicationLink")
                    posted = parse_datetime(int(j["pubDate"]) if str(j.get("pubDate")).isdigit() else j.get("pubDate"))
                    if posted and posted < limit:
                        old = True
                        continue
                    if gid in seen or not filters.title_in_family(j.get("title"))[0]:
                        continue
                    seen.add(gid)
                    restrictions = j.get("locationRestrictions") or []
                    salary = (
                        f"{j.get('minSalary')}-{j.get('maxSalary')} {j.get('currency')} ({j.get('salaryPeriod')})"
                        if j.get("minSalary") or j.get("maxSalary")
                        else None
                    )
                    yield Posting(
                        id=(gid or "").rstrip("/").split("/")[-1],
                        title=j.get("title") or "",
                        company=j.get("companyName"),
                        location=", ".join(restrictions) or "Worldwide (remote)",
                        url=gid,
                        apply_url=j.get("applicationLink"),
                        description=strip_html(j.get("description")),
                        posted_at=posted,
                        remote=True,
                        salary=salary,
                        extra={"himalayas": j, "query": keyword},
                    )
                if old or len(jobs) < 20:
                    break
