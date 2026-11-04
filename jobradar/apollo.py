"""Single Apollo client: companies (enrich, search) and people (search, reveal email).

**Corporate email only**: phone, waterfall, and personal emails are always False
and are checked before each call that spends credits. Only HTTP 429 is retried
(nothing was executed), so a failure never charges twice. Responses are stored
raw; people responses are reduced to useful fields with a deletion deadline (Law 21.719).

CLI::

    python -m jobradar.apollo credits
    python -m jobradar.apollo search acme.com --titles "CEO,Head of Growth" [--seniorities c_suite,vp]
    python -m jobradar.apollo reveal --id <apollo_person_id> [--id ...]
    python -m jobradar.apollo reveal --linkedin https://www.linkedin.com/in/x [--name "Ana Pérez" --domain acme.com]
"""

from __future__ import annotations

import argparse
import json
import re
from typing import Any

from jobradar import config, http_client, raw

API = "https://api.apollo.io/api/v1"
FORBIDDEN_FLAGS = ("reveal_phone_number", "run_waterfall_phone", "run_waterfall_email", "reveal_personal_emails")
DEFAULT_SENIORITIES = ["owner", "founder", "c_suite", "partner", "vp", "head", "director", "manager"]


class ApolloError(RuntimeError):
    """Apollo responded with an error or something unexpected."""


def configured() -> bool:
    return bool(config.get("APOLLO_API_KEY"))


def _headers() -> dict[str, str]:
    return {
        "X-Api-Key": config.require("APOLLO_API_KEY"),
        "Content-Type": "application/json",
        "Cache-Control": "no-cache",
        "accept": "application/json",
    }


def compact(person: dict | None) -> dict | None:
    """Useful fields of an Apollo person (the only thing saved about them)."""
    if not person:
        return None
    org = person.get("organization") or {}
    return {
        "id": person.get("id"),
        "name": person.get("name") or " ".join(filter(None, [person.get("first_name"), person.get("last_name")])),
        "first_name": person.get("first_name"),
        "title": person.get("title"),
        "seniority": person.get("seniority"),
        "linkedin_url": person.get("linkedin_url"),
        "email": person.get("email"),
        "email_status": person.get("email_status"),
        "has_email": person.get("has_email"),
        "country": person.get("country"),
        "city": person.get("city"),
        "organization": org.get("name"),
        "organization_domain": org.get("primary_domain") or org.get("website_url"),
    }


def _save_raw(path: str, query: Any, data: dict[str, Any]) -> None:
    if path.startswith("users/"):
        return
    people = path.startswith(("mixed_people", "people/"))
    if people:
        data = {
            "pagination": data.get("pagination"),
            "people": [compact(p) for p in data.get("people") or []],
            "matches": [compact(p) for p in data.get("matches") or []],
        }
    raw.append("apollo", [data], third_party=people, path=path, query=query)


def request(path: str, body: dict | None = None, *, params: dict | None = None, timeout: float = 90) -> dict[str, Any]:
    """GET (without ``body``) or POST to the API. ``ApolloError`` if it fails."""
    resp = http_client.request(
        "POST" if body is not None else "GET",
        f"{API}/{path}",
        json=body,
        params=params,
        headers=_headers(),
        timeout=timeout,
        retries=2,
        backoff=20.0,
        retry_status=frozenset({429}),
    )
    if not resp.ok:
        raise ApolloError(f"Apollo {path}: HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        data = resp.json()
    except ValueError as exc:
        raise ApolloError(f"Apollo {path}: response is not JSON") from exc
    if not isinstance(data, dict):
        raise ApolloError(f"Apollo {path}: unexpected JSON")
    _save_raw(path, body if body is not None else params, data)
    return data


def credits() -> int:
    """Remaining credits (reading it does not spend)."""
    profile = request("users/api_profile", params={"include_credit_usage": "true"})
    match = re.search(r'"num_credits_remaining"\s*:\s*"?(\d+)', json.dumps(profile))
    if not match:
        raise ApolloError("Apollo did not return num_credits_remaining")
    return int(match.group(1))


def search_people(
    domain: str,
    *,
    titles: list[str] | None = None,
    seniorities: list[str] | None = None,
    per_page: int = 25,
    page: int = 1,
) -> list[dict[str, Any]]:
    """People of the company (without email: id, name, title, linkedin_url, has_email). 0 credits."""
    body: dict[str, Any] = {"q_organization_domains_list": [domain], "page": page, "per_page": per_page}
    if titles:
        body["person_titles"] = titles
    if seniorities is None and not titles:
        seniorities = DEFAULT_SENIORITIES
    if seniorities:
        body["person_seniorities"] = seniorities
    return request("mixed_people/api_search", body).get("people") or []


def _safe_payload(details: list[dict]) -> dict[str, Any]:
    payload = {"details": details, **{flag: False for flag in FORBIDDEN_FLAGS}}
    if any(payload.get(flag) is not False for flag in FORBIDDEN_FLAGS) or "webhook_url" in payload:
        raise ApolloError("ABORT: the request could reveal phones or personal emails")
    return payload


def reveal(details: list[dict]) -> list[dict | None]:
    """Corporate email via ``people/bulk_match`` (max 10 per call; 1 credit per match).

    Each detail brings ``id`` (Apollo), ``linkedin_url``, or ``first_name``/``last_name``/``domain``.
    """
    out: list[dict | None] = []
    for i in range(0, len(details), 10):
        out.extend(request("people/bulk_match", _safe_payload(details[i : i + 10])).get("matches") or [])
    return out


def enrich_organization(domain: str, *, timeout: float = 20) -> dict[str, Any]:
    """Company profile by domain (``organizations/enrich``)."""
    return request("organizations/enrich", params={"domain": domain}, timeout=timeout).get("organization") or {}


def search_organizations(body: dict[str, Any], *, timeout: float = 40) -> dict[str, Any]:
    """Company search (``mixed_companies/search``): Apollo filters in ``body``."""
    return request("mixed_companies/search", body, timeout=timeout)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Apollo: search decision makers and reveal their corporate email")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("credits")
    s = sub.add_parser("search")
    s.add_argument("domain")
    s.add_argument("--titles", default="")
    s.add_argument("--seniorities", default="")
    s.add_argument("--per-page", type=int, default=25)
    r = sub.add_parser("reveal")
    r.add_argument("--id", action="append", default=[])
    r.add_argument("--linkedin", action="append", default=[])
    r.add_argument("--name", default="")
    r.add_argument("--domain", default="")
    args = ap.parse_args(argv)

    if args.cmd == "credits":
        print(json.dumps({"credits_remaining": credits()}))
    elif args.cmd == "search":
        titles = [t.strip() for t in args.titles.split(",") if t.strip()] or None
        seniorities = [t.strip() for t in args.seniorities.split(",") if t.strip()] or None
        people = search_people(args.domain, titles=titles, seniorities=seniorities, per_page=args.per_page)
        print(json.dumps([compact(p) for p in people], ensure_ascii=False, indent=1))
    else:
        details = [{"id": i} for i in args.id] + [{"linkedin_url": u} for u in args.linkedin]
        if args.name and args.domain:
            first, _, last = args.name.partition(" ")
            details.append({"first_name": first, "last_name": last, "domain": args.domain})
        print(json.dumps([compact(m) for m in reveal(details)], ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
