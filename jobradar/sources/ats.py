"""Public APIs of the most used ATS (Ashby, Greenhouse, Lever, Workable), without keys or cost.

- ``parse_url``: (ats, board, job id) from an application link.
- ``list_board``: all jobs published on a company's board.
- ``detail``: a job by its link (description, date, location); marks ``closed`` if the ATS
  no longer publishes it. If the link is not from a known ATS, the page's JSON-LD can be read.

Responses are cached for 6 hours (boards change little during a day) and
a per-domain pause is respected.
"""

from __future__ import annotations

import html
import json
import re
from dataclasses import dataclass
from typing import Any
from urllib.parse import parse_qs, quote, unquote, urlparse

from jobradar import config, http_client
from jobradar.dates import parse_datetime
from jobradar.sources.base import Posting
from jobradar.text import strip_html

_cache: http_client.DiskCache | None = None
ATS_PAUSE = 0.4  # the public ATS APIs handle ~2-3 req/s
PAGE_PAUSE = 1.0


def _get(
    url: str, *, pause: float = ATS_PAUSE, as_json: bool = True, method: str = "GET", body: dict | None = None
) -> Any:
    """GET (or POST) with cache; ``None`` if it doesn't exist (404/410), is blocked, or isn't JSON."""
    global _cache
    if _cache is None:
        _cache = http_client.DiskCache(config.DATA_DIR / "cache" / "ats", ttl=6 * 3600)
    key = http_client.DiskCache.key(method, url, body)
    text = _cache.get(key)
    if text is None:
        resp = http_client.request(method, url, json=body, min_interval=pause, timeout=40, retries=2)
        if resp.status_code != 200:
            return None
        text = resp.text
        _cache.set(key, text)
    if not as_json:
        return text
    try:
        return json.loads(text)
    except ValueError:
        return None


def parse_url(url: str) -> tuple[str, str, str | None] | None:
    """(ats, board, id) from an Ashby/Greenhouse/Lever/Workable link; ``None`` if it's another site."""
    parts_url = urlparse((url or "").strip())
    host = parts_url.netloc.lower()
    parts = [unquote(x) for x in parts_url.path.split("/") if x]
    if host == "jobs.ashbyhq.com" and parts:
        return "ashby", parts[0], parts[1] if len(parts) > 1 else None
    if host.endswith("greenhouse.io"):
        query = parse_qs(parts_url.query)
        if "for" in query:
            return "greenhouse", query["for"][0], (query.get("token") or [None])[0]
        if parts and parts[0] != "embed":
            return "greenhouse", parts[0], parts[2] if len(parts) > 2 and parts[1] == "jobs" else None
    if host in ("jobs.lever.co", "jobs.eu.lever.co") and parts:
        return "lever", parts[0], parts[1] if len(parts) > 1 else None
    if host == "apply.workable.com" and parts:
        return "workable", parts[0], parts[2] if len(parts) > 2 and parts[1] == "j" else None
    return None


def _pretty(slug: str) -> str:
    return " ".join(w.capitalize() for w in re.sub(r"[-_.]+", " ", slug).split())


def _join(*values: Any) -> str:
    return " | ".join(dict.fromkeys(str(v) for v in values if v))


# --- List a company's board ---------------------------------------------------------


def _ashby(board: str) -> list[Posting]:
    data = _get(f"https://api.ashbyhq.com/posting-api/job-board/{quote(board)}?includeCompensation=true")
    if not data or "jobs" not in data:
        return []
    org = _get(
        "https://jobs.ashbyhq.com/api/non-user-graphql?op=ApiOrganizationFromHostedJobsPageName",
        method="POST",
        body={
            "operationName": "ApiOrganizationFromHostedJobsPageName",
            "variables": {"organizationHostedJobsPageName": board},
            "query": "query ApiOrganizationFromHostedJobsPageName($organizationHostedJobsPageName: String!) "
            "{ organization: organizationFromHostedJobsPageName(organizationHostedJobsPageName: "
            "$organizationHostedJobsPageName) { name publicWebsite } }",
        },
    )
    company = ((org or {}).get("data") or {}).get("organization") or {}
    out = []
    for j in data["jobs"]:
        if j.get("isListed") is False:
            continue
        address = (j.get("address") or {}).get("postalAddress") or {}
        location = _join(
            j.get("location"),
            *[s.get("location") for s in j.get("secondaryLocations") or []],
            address.get("addressLocality"),
            address.get("addressRegion"),
            address.get("addressCountry"),
        )
        comp = j.get("compensation") or {}
        out.append(
            Posting(
                id=str(j["id"]),
                title=j.get("title") or "",
                company=company.get("name") or _pretty(board),
                location=location,
                url=j.get("jobUrl"),
                apply_url=j.get("applyUrl"),
                description=j.get("descriptionPlain") or strip_html(j.get("descriptionHtml")),
                posted_at=parse_datetime(j.get("publishedAt")),
                remote=bool(j.get("isRemote")) or j.get("workplaceType") == "Remote",
                salary=comp.get("scrapeableCompensationSalarySummary") or comp.get("compensationTierSummary"),
                extra={
                    "ats": "ashby",
                    "board": board,
                    "workplace_type": j.get("workplaceType"),
                    "company_website": company.get("publicWebsite"),
                },
            )
        )
    return out


def _greenhouse(board: str) -> list[Posting]:
    data = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs?content=true")
    if not data or "jobs" not in data:
        return []
    name = (_get(f"https://boards-api.greenhouse.io/v1/boards/{board}") or {}).get("name") or _pretty(board)
    out = []
    for j in data["jobs"]:
        offices = [
            (o.get("name") or "") + (" " + o["location"] if o.get("location") else "") for o in j.get("offices") or []
        ]
        location = _join((j.get("location") or {}).get("name"), *offices)
        out.append(
            Posting(
                id=str(j["id"]),
                title=j.get("title") or "",
                company=j.get("company_name") or name,
                location=location,
                url=j.get("absolute_url"),
                apply_url=j.get("absolute_url"),
                description=strip_html(j.get("content")),
                posted_at=parse_datetime(j.get("first_published") or j.get("updated_at")),
                remote=bool(re.search(r"remote", location, re.I)),
                extra={"ats": "greenhouse", "board": board},
            )
        )
    return out


def _lever_description(j: dict) -> str:
    parts = [j.get("descriptionPlain") or strip_html(j.get("description"))]
    parts += [(lst.get("text") or "") + "\n" + strip_html(lst.get("content")) for lst in j.get("lists") or []]
    parts.append(j.get("additionalPlain") or strip_html(j.get("additional")))
    return "\n\n".join(p for p in parts if p)


def _lever(board: str) -> list[Posting]:
    base, data = "https://jobs.lever.co", _get(f"https://api.lever.co/v0/postings/{board}?mode=json")
    if not isinstance(data, list):
        base, data = "https://jobs.eu.lever.co", _get(f"https://api.eu.lever.co/v0/postings/{board}?mode=json")
    if not isinstance(data, list):
        return []
    page = _get(f"{base}/{board}", as_json=False, pause=PAGE_PAUSE) or ""
    match = re.search(r"<title>(.*?)</title>", page, re.S)
    name = html.unescape(match.group(1)).replace(" jobs", "").strip() if match else _pretty(board)
    out = []
    for j in data:
        cats = j.get("categories") or {}
        range_ = j.get("salaryRange")
        salary = (
            f"{range_.get('currency')} {range_.get('min')}-{range_.get('max')} {range_.get('interval')}"
            if range_
            else j.get("salaryDescriptionPlain")
        )
        out.append(
            Posting(
                id=str(j["id"]),
                title=j.get("text") or "",
                company=name,
                location=_join(
                    cats.get("location"), *(cats.get("allLocations") or []), j.get("country"), j.get("workplaceType")
                ),
                url=j.get("hostedUrl"),
                apply_url=j.get("applyUrl"),
                description=_lever_description(j),
                posted_at=parse_datetime(j.get("createdAt")),
                remote=j.get("workplaceType") == "remote",
                salary=salary,
                countries=[j["country"]] if j.get("country") else [],
                extra={"ats": "lever", "board": board, "team": cats.get("team"), "commitment": cats.get("commitment")},
            )
        )
    return out


def _workable(board: str) -> list[Posting]:
    data = _get(f"https://apply.workable.com/api/v1/widget/accounts/{board}")
    if not data or "jobs" not in data:
        return []
    out = []
    for j in data["jobs"]:
        places = [
            ", ".join(x for x in (p.get("city"), p.get("region"), p.get("country")) if x)
            for p in j.get("locations") or []
        ]
        places.append(", ".join(x for x in (j.get("city"), j.get("state"), j.get("country")) if x))
        # Workable's listing doesn't include the description: ``fill_in`` fetches it later, only
        # for jobs that pass the title filter.
        out.append(
            Posting(
                id=f"{board}/{j['shortcode']}",
                title=j.get("title") or "",
                company=data.get("name") or _pretty(board),
                location=_join(*places, "Remote" if j.get("telecommuting") else None),
                url=f"https://apply.workable.com/{board}/j/{j['shortcode']}/",
                apply_url=j.get("application_url"),
                posted_at=parse_datetime(j.get("published_on") or j.get("created_at")),
                remote=bool(j.get("telecommuting")),
                countries=[p["countryCode"] for p in j.get("locations") or [] if p.get("countryCode")],
                extra={"ats": "workable", "board": board},
            )
        )
    return out


LISTERS = {"ashby": _ashby, "greenhouse": _greenhouse, "lever": _lever, "workable": _workable}


def list_board(ats: str, board: str) -> list[Posting]:
    """Jobs published on the ``board`` of the ATS ``ats``; ``[]`` if it doesn't exist."""
    return LISTERS[ats](board)


# --- A job by its link -------------------------------------------------------------------


@dataclass
class Detail:
    closed: bool = False
    title: str | None = None
    company: str | None = None
    location: str | None = None
    description: str | None = None
    posted_at: Any = None
    remote: bool | None = None
    ats: str | None = None
    board: str | None = None


def detail(url: str, *, read_page: bool = False) -> Detail | None:
    """Full data of a job by its link. ``None`` if nothing could be determined.

    With ``read_page``, if the link is not from a known ATS, the page's JSON-LD is read.
    """
    seed = parse_url(url)
    if seed and seed[2]:
        return _ats_detail(*seed)
    if read_page and not re.search(r"linkedin\.com|notion\.(so|site|com)", url or ""):
        return _detail_page(url)
    return None


def _ats_detail(ats: str, board: str, jid: str) -> Detail | None:
    closed = Detail(closed=True, ats=ats, board=board)
    if ats == "ashby":
        postings = _ashby(board)
        found = next((a for a in postings if a.id == str(jid)), None)
        if not found:
            return closed if postings else None
        return Detail(
            title=found.title,
            company=found.company,
            location=found.location,
            description=found.description,
            posted_at=found.posted_at,
            remote=found.remote,
            ats=ats,
            board=board,
        )
    if ats == "greenhouse":
        d = _get(f"https://boards-api.greenhouse.io/v1/boards/{board}/jobs/{jid}")
        if not d or not d.get("id"):
            return closed
        location = _join((d.get("location") or {}).get("name"), *[o.get("name") for o in d.get("offices") or []])
        return Detail(
            title=d.get("title"),
            company=d.get("company_name") or _pretty(board),
            location=location,
            description=strip_html(d.get("content")),
            posted_at=parse_datetime(d.get("first_published") or d.get("updated_at")),
            remote=bool(re.search("remote", location, re.I)),
            ats=ats,
            board=board,
        )
    if ats == "lever":
        d = _get(f"https://api.lever.co/v0/postings/{board}/{jid}")
        if not isinstance(d, dict) or not d.get("id"):
            return closed
        cats = d.get("categories") or {}
        return Detail(
            title=d.get("text"),
            company=_pretty(board),
            location=_join(
                cats.get("location"), *(cats.get("allLocations") or []), d.get("country"), d.get("workplaceType")
            ),
            description=_lever_description(d),
            posted_at=parse_datetime(d.get("createdAt")),
            remote=d.get("workplaceType") == "remote",
            ats=ats,
            board=board,
        )
    if ats == "workable":
        d = _get(f"https://apply.workable.com/api/v2/accounts/{board}/jobs/{jid}")
        if not d:
            return closed
        place = d.get("location") or {}
        location = ", ".join(x for x in (place.get("city"), place.get("region"), place.get("country")) if x)
        if d.get("remote") or d.get("workplace") == "remote":
            location = (location + " | Remote").strip(" |")
        return Detail(
            title=d.get("title"),
            company=_pretty(board),
            location=location,
            description="\n\n".join(
                strip_html(d.get(k)) for k in ("description", "requirements", "benefits") if d.get(k)
            ),
            posted_at=parse_datetime(d.get("published") or d.get("created")),
            remote=bool(d.get("remote")),
            ats=ats,
            board=board,
        )
    return None


def jsonld_jobposting(page: str) -> dict | None:
    """The first ``JobPosting`` from a page's JSON-LD."""
    for match in re.finditer(r"(?is)<script[^>]+application/ld\+json[^>]*>(.*?)</script>", page or ""):
        try:
            data = json.loads(match.group(1).strip())
        except ValueError:
            continue
        candidates = data if isinstance(data, list) else data.get("@graph", [data]) if isinstance(data, dict) else []
        for c in candidates:
            kind = c.get("@type") if isinstance(c, dict) else None
            if kind == "JobPosting" or (isinstance(kind, list) and "JobPosting" in kind):
                return c
    return None


def _address(value: Any) -> str:
    if isinstance(value, str):
        return value
    if not isinstance(value, dict):
        return ""
    addr = value.get("address", value)
    if isinstance(addr, str):
        return addr
    country = addr.get("addressCountry")
    country = country if isinstance(country, str) else (country or {}).get("name")
    return ", ".join(str(x) for x in (addr.get("addressLocality"), addr.get("addressRegion"), country) if x)


def _detail_page(url: str) -> Detail | None:
    page = _get(url, as_json=False, pause=PAGE_PAUSE)
    job = jsonld_jobposting(page or "")
    if not job:
        return None
    places = job.get("jobLocation")
    places = places if isinstance(places, list) else [places] if places else []
    parts = [p for p in (_address(x) for x in places) if p]
    remote = None
    if str(job.get("jobLocationType") or "").upper() == "TELECOMMUTE":
        remote = True
        parts.append("Remote")
    reqs = job.get("applicantLocationRequirements")
    for req in reqs if isinstance(reqs, list) else [reqs] if reqs else []:
        name = req.get("name") if isinstance(req, dict) else req
        if name:
            parts.append(str(name))
    org = job.get("hiringOrganization")
    return Detail(
        title=html.unescape(job.get("title") or ""),
        company=org.get("name") if isinstance(org, dict) else org,
        location=_join(*parts),
        description=strip_html(job.get("description")),
        posted_at=parse_datetime(job.get("datePosted")),
        remote=remote,
    )


def fill_in(posting: Posting, *, read_page: bool = False) -> bool:
    """Complete the job's description, date, and location from its ATS. ``False`` if it's already closed."""
    info = detail(posting.url or posting.apply_url or "", read_page=read_page)
    if info is None and posting.apply_url and posting.apply_url != posting.url:
        info = detail(posting.apply_url, read_page=read_page)
    if info is None:
        return True
    if info.closed:
        return False
    if info.description and len(info.description) > len(posting.description or ""):
        posting.description = info.description
    posting.posted_at = posting.posted_at or info.posted_at
    if info.location and info.location.lower() not in (posting.location or "").lower():
        posting.location = _join(posting.location, info.location)
    if posting.remote is None:
        posting.remote = info.remote
    posting.extra.update({k: v for k, v in (("ats", info.ats), ("board", info.board)) if v})
    return True
