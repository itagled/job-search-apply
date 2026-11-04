"""Glassdoor and Wellfound via Apify actors (paid: only with ``--paid``).

Each search has a spending cap (``maxTotalChargeUsd``) calculated with the actor's price; the
actual cost of each run (``usageTotalUsd``) is kept in the raw data.
"""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import Any

from jobradar import apify, profile
from jobradar.dates import parse_datetime
from jobradar.sources.base import Connector, Posting, register
from jobradar.text import strip_html

_REMOTE = re.compile(r"remote|remoto|home\s*office|work\s*from\s*home", re.I)


def _text(value: Any) -> str:
    return strip_html(value) if isinstance(value, str) else ""


def _cap(start_price: float, posting_price: float, maximum: int) -> float:
    return round(start_price + maximum * posting_price * 1.3 + 0.003, 4)


@register
class Glassdoor(Connector):
    name = "glassdoor"
    description = "Glassdoor via Apify (valig/glassdoor-jobs-scraper): remote abroad and in the home country"
    paid = True
    ACTOR = "valig/glassdoor-jobs-scraper"
    START_PRICE, POSTING_PRICE = 0.0008, 0.00032

    def search(self, *, days: int) -> Iterable[Posting]:
        prof = profile.load()
        # remote roles abroad (glassdoor_en) and roles in the home country (glassdoor_local)
        searches = [
            (
                prof.search.for_source("glassdoor_remote_location", "United States"),
                True,
                prof.search.for_source("glassdoor_en"),
                50,
            )
        ]
        if prof.location.home_name:
            searches.append((prof.location.home_name, False, prof.search.for_source("glassdoor_local"), 40))
        for place, remote, titles, maximum in searches:
            for title in titles:
                payload = {
                    "keywords": title,
                    "location": place,
                    "daysOld": max(1, days),
                    "limit": maximum,
                    "sortBy": "relevant_desc",
                }
                if remote:
                    payload["remoteWorkType"] = True
                run = apify.run(
                    self.ACTOR,
                    payload,
                    accept_partial=True,
                    raw_kind=f"sources/{self.name}",
                    max_total_charge_usd=_cap(self.START_PRICE, self.POSTING_PRICE, maximum),
                )
                for item in run.items:
                    yield self._posting(item, remote=remote, search_query=title, place=place)

    @staticmethod
    def _posting(item: dict, *, remote: bool, search_query: str, place: str) -> Posting:
        location = item.get("location")
        location = str((location.get("name") if isinstance(location, dict) else location) or "")
        is_remote = remote or bool(_REMOTE.search(location))
        if is_remote and "remot" not in location.lower():
            location = f"{location} (Remote)".strip()
        age = item.get("ageInDays")
        posted = datetime.now(UTC) - timedelta(days=int(age)) if isinstance(age, (int, float)) else None
        return Posting(
            id=str(item.get("id")),
            title=item.get("title") or "",
            company=(item.get("employer") or {}).get("name"),
            location=location,
            url=item.get("seoUrl") or item.get("url"),
            apply_url=item.get("url"),
            description=_text(item.get("description")),
            posted_at=posted,
            remote=is_remote,
            salary=str(item["pay"]) if item.get("pay") else None,
            extra={"search_query": search_query, "country_search": place, "glassdoor": item},
        )


@register
class Wellfound(Connector):
    name = "wellfound"
    description = "Wellfound via Apify (memo23/wellfound-jobs-scraper): remote roles by role page and title words"
    paid = True
    ACTOR = "memo23/wellfound-jobs-scraper"

    def search(self, *, days: int) -> Iterable[Posting]:
        limit = datetime.now(UTC) - timedelta(days=days)
        for label, roles, title_filter, by_role, cap in self._runs():
            payload = {
                "startUrls": [f"https://wellfound.com/role/r/{r}" for r in roles],
                "jobTitle": title_filter,
                "remoteOnly": True,
                "sort": "newest",
                "maxItems": by_role,
                "enrichJobDetail": False,
                "enrichCompanyProfile": False,
                "enrichEmails": False,
            }
            run = apify.run(
                self.ACTOR, payload, max_total_charge_usd=cap, accept_partial=True, raw_kind=f"sources/{self.name}"
            )
            for item in run.items:
                posting = self._posting(item, label)
                if posting and (posting.posted_at is None or posting.posted_at >= limit):
                    yield posting

    @staticmethod
    def _runs() -> list[tuple[str, list[str], str, int, float]]:
        """(label, Wellfound role pages, title words, max per role, USD cap). By default the role pages
        come from the keywords (``data analyst`` -> ``data-analyst``)."""
        search = profile.load().search
        words = ", ".join(search.keywords)
        runs = [
            (
                "remote_ops",
                search.for_source(
                    "wellfound_roles_ops", [re.sub(r"\W+", "-", k.lower()).strip("-") for k in search.keywords]
                ),
                search.for_source("wellfound_title_ops", words),
                60,
                0.45,
            )
        ]
        if search.for_source("wellfound_roles_eng", []):
            runs.append(
                (
                    "remote_eng",
                    search.for_source("wellfound_roles_eng"),
                    search.for_source("wellfound_title_eng", words),
                    25,
                    0.15,
                )
            )
        return runs

    @staticmethod
    def _posting(item: dict, label: str) -> Posting | None:
        if not item.get("jobId"):
            return None
        accepted = item.get("acceptedRemoteLocations") or []
        remote = f"Remote ({', '.join(accepted) if accepted else 'anywhere'})" if item.get("remote") else ""
        return Posting(
            id=f"wellfound:{item['jobId']}",
            title=item.get("jobTitle") or "",
            company=item.get("companyName"),
            location=" | ".join(x for x in (", ".join(item.get("locations") or []), remote) if x),
            url=item.get("jobUrl"),
            apply_url=item.get("applyUrl") or item.get("jobUrl"),
            description=_text(item.get("jobDescription")),
            posted_at=parse_datetime(item.get("postedAt")),
            remote=bool(item.get("remote")),
            salary=str(item["compensation"]) if item.get("compensation") else None,
            extra={"accepted_remote_locations": accepted, "run": label, "wellfound": item},
        )
