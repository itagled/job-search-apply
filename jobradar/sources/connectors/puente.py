"""Puente Talent (LATAM remote talent recruiter): job posts from its sitemap, with the JSON-LD of each listing."""

from __future__ import annotations

import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from jobradar import http_client
from jobradar.dates import parse_datetime
from jobradar.sources import ats, filters, titles
from jobradar.sources.base import Connector, Posting, register
from jobradar.text import strip_html

SITEMAP = "https://puentetalent.com/sitemap.xml"


def _slug(url: str) -> str:
    """The title, from the link of a listing."""
    return re.sub(r"-\d+$", "", url.rsplit("/", 1)[-1]).replace("-", " ")


@register
class PuenteTalent(Connector):
    name = "web_puente"
    description = "Puente Talent: remote jobs for LATAM (sitemap + JSON-LD of each posting)"

    def search(self, *, days: int) -> Iterable[Posting]:
        since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
        mapping = http_client.get(SITEMAP, min_interval=1.0, timeout=40).text
        urls = re.findall(r"<loc>(https://puentetalent\.com/jobs/[^<]+)</loc>\s*<lastmod>([^<]*)</lastmod>", mapping)
        recent = [(url, lastmod) for url, lastmod in urls if (lastmod or "9999")[:10] >= since]
        titles.judge(_slug(url) for url, _ in recent)
        for url, lastmod in recent:
            jid = re.search(r"-(\d+)$", url)
            slug = _slug(url)
            if not jid or not filters.title_in_family(slug)[0]:
                continue  # the title is in the link: it is discarded without fetching the listing
            page = http_client.get(url, min_interval=1.0, timeout=40).text
            job = ats.jsonld_jobposting(page)
            if not job:
                continue
            self.save_raw(job, url=url, lastmod=lastmod)
            requirements = [
                x["name"]
                for x in job.get("applicantLocationRequirements") or []
                if isinstance(x, dict) and x.get("name")
            ]
            salary = job.get("baseSalary") if isinstance(job.get("baseSalary"), dict) else {}
            value = salary.get("value") or {}
            yield Posting(
                id=f"puente:{jid.group(1)}",
                title=job.get("title") or slug,
                company="Puente Talent Partners",
                location=" | ".join([*requirements, job.get("jobLocationType") or "", "Remote (Latin America)"]).strip(
                    " |"
                ),
                url=url,
                apply_url=url,
                description=strip_html(job.get("description")),
                posted_at=parse_datetime(job.get("datePosted") or lastmod),
                remote=True,
                salary=(
                    f"{value.get('minValue')}-{value.get('maxValue')} {salary.get('currency')}/{value.get('unitText')}"
                    if value
                    else None
                ),
                extra={
                    "recruiter": "Puente Talent Partners",
                    "employment_type": job.get("employmentType"),
                    "valid_through": job.get("validThrough"),
                    "lastmod": lastmod,
                },
            )
