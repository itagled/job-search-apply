"""Cargo, portal for "GTM engineer jobs" (getcargo.ai/jobs, data from TheirStack).

The ~1,100 postings are embedded in the page (payload ``self.__next_f`` from Next.js). Those that
pass the title filter and are within reach are completed with their original source: the
public ATS API, the page's JSON-LD, or the public LinkedIn profile. Closed ones are discarded.
"""

from __future__ import annotations

import hashlib
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from jobradar import http_client, profile
from jobradar.dates import parse_datetime
from jobradar.sources import ats, filters, linkedin_public, titles
from jobradar.sources.base import Connector, Posting, register

PORTAL = "https://www.getcargo.ai/jobs"
UA = "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/126.0 Safari/537.36"
_PUSH = re.compile(r'self\.__next_f\.push\(\[1,"((?:[^"\\]|\\.)*)"\]\)')
_LATAM_COUNTRIES = {
    "Chile",
    "Argentina",
    "Brazil",
    "Mexico",
    "Colombia",
    "Peru",
    "Uruguay",
    "Guatemala",
    "Costa Rica",
    "Ecuador",
    "Venezuela",
    "Panama",
    "Paraguay",
    "Bolivia",
    "Dominican Republic",
    "El Salvador",
    "Honduras",
    "Nicaragua",
    "Puerto Rico",
}
_ATS_WITH_API = re.compile(r"jobs\.ashbyhq\.com|greenhouse\.io|jobs\.(eu\.)?lever\.co|apply\.workable\.com")
# Signals of "US-only / Europe-only" in remote postings without a clear country.
_FOREIGN_ONLY = re.compile(
    r"remote\s*[\(\-–,]\s*(u\.s\.?|usa?|united\s*states)(?![a-z])|\(\s*u\.s\.?[\s-]*based\s*\)|"
    r"overlap[^.\n]{0,60}(german|cet|cest|central\s*european|european|uk|gmt|bst|indian|ist|australian|aest|apac)\s*"
    r"(time|business\s*hours|working\s*hours)",
    re.I,
)
_WORK_MODE = {"remote": "Remote", "hybrid": "Hybrid", "onSite": "On-site"}


def parse(page: str) -> list[dict]:
    """Postings embedded in the page HTML (without duplicates)."""
    text = "".join(json.loads('"' + chunk + '"') for chunk in _PUSH.findall(page))
    decoder, jobs, seen = json.JSONDecoder(), [], set()
    for match in re.finditer(r'"jobs":\[', text):
        array, _ = decoder.raw_decode(text, match.end() - 1)
        for job in array:
            key = (job.get("jobUrl"), job.get("jobTitle"), job.get("companyName")) if isinstance(job, dict) else None
            if key and job.get("jobTitle") and key not in seen:
                seen.add(key)
                jobs.append(job)
    return jobs


def _in_reach(job: dict) -> bool:
    """Worth completing: remote, in the region, no country, or in an ATS that must be checked anyway."""
    if profile.load().location.preset == "any":
        return True
    loc = f"{job.get('companyLocation') or ''} | {job.get('country') or ''}"
    latam = (
        job.get("country") in _LATAM_COUNTRIES
        or filters.RE_LATAM_C.search(loc)
        or filters.home_matches(loc)
        or filters.RE_LOC_GLOBAL.search(loc)
        or filters.RE_LOC_GLOBAL.search(job.get("jobTitle") or "")
    )
    return (
        job.get("jobLocation") == "remote"
        or bool(latam)
        or not job.get("country")
        or bool(_ATS_WITH_API.search(job.get("jobUrl") or ""))
    )


def _id(url: str) -> str:
    seed = ats.parse_url(url)
    if seed and seed[2]:
        return f"{seed[0]}:{seed[2]}"
    linkedin = re.search(r"linkedin\.com/.*?(\d{6,})", url)
    if linkedin:
        return f"linkedin:{linkedin.group(1)}"
    return "cargo:" + hashlib.sha1(url.split("?")[0].encode()).hexdigest()[:12]


@register
class Cargo(Connector):
    name = "portal_cargo"
    niche = "GTM engineering and RevOps roles"
    description = "Cargo (getcargo.ai/jobs): GTM job board, completed from the ATS or LinkedIn"

    def search(self, *, days: int) -> Iterable[Posting]:
        limit = datetime.now(UTC) - timedelta(days=days)
        page = http_client.get(PORTAL, headers={"User-Agent": UA}, timeout=60).text
        jobs = parse(page)
        self.save_raw(jobs, portal=PORTAL)
        titles.judge(job.get("jobTitle") for job in jobs)
        for job in jobs:
            title, url = job.get("jobTitle") or "", (job.get("jobUrl") or "").strip()
            posted = parse_datetime(job.get("datePosted"))
            if not url or (posted and posted < limit) or not filters.title_in_family(title)[0] or not _in_reach(job):
                continue
            posting = self._fill_in(job, title, url, posted)
            if posting:
                yield posting

    def _fill_in(self, job: dict, title: str, url: str, posted: datetime | None) -> Posting | None:
        work_mode = _WORK_MODE.get(job.get("jobLocation"), "")
        place = " | ".join(x for x in (job.get("companyLocation"), job.get("country")) if x)
        posting = Posting(
            id=_id(url),
            title=title,
            company=job.get("companyName"),
            location=" | ".join(x for x in (place, work_mode) if x),
            url=url,
            apply_url=url,
            description=job.get("jobDescription"),
            posted_at=posted,
            remote=job.get("jobLocation") == "remote",
            extra={"cargo_job": job, "portal": "getcargo.ai/jobs", "description_source": "cargo_summary"},
        )
        linkedin = re.search(r"linkedin\.com/.*?(\d{6,})", url)
        if linkedin:
            detail_page = linkedin_public.detail_page(linkedin.group(1))
            if detail_page:
                posting.description = detail_page["description"]
                posting.location = detail_page.get("location") or posting.location
                posting.extra["description_source"] = "linkedin"
        else:
            info = ats.detail(url, read_page=True)
            if info and info.closed:
                return None
            if info and info.description:
                posting.description = info.description
                posting.extra["description_source"] = info.ats or "jsonld"
            if info and len(re.findall(r"[A-Za-z]", info.location or "")) >= 3:
                posting.location = info.location  # the original profile takes precedence over what Cargo says
            posting.posted_at = info.posted_at if info and info.posted_at else posting.posted_at
        ok, why = filters.eligible(posting.title, posting.location, posting.description)
        desc = posting.description or ""
        if ok and why.startswith("ambiguous") and _FOREIGN_ONLY.search(desc) and not filters.hires_in_region(desc):
            return None
        return posting
