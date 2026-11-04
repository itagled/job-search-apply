"""LinkedIn: the public (free) search, the Apify actor (paid) and the alerts arriving in Gmail.

The three sources share the LinkedIn job id. Before opening a listing, it checks whether the
job is already in the DB with a description (via any of the three channels), to avoid requesting it again.
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime

from sqlalchemy import func, select

from jobradar import profile
from jobradar.dates import parse_datetime
from jobradar.sources import filters, linkedin_alerts, linkedin_apify, linkedin_public, titles
from jobradar.sources.base import Connector, Posting, register

LINKEDIN_SOURCES = ("linkedin_jobs_guest", "linkedin_jobs_valig", "linkedin_job_alert_email", "linkedin_jobs_apify")


def _window(days: int) -> str:
    return "24h" if days <= 1 else "week" if days <= 7 else "month"


def _with_description(ids: list[str]) -> set[str]:
    """LinkedIn ids already in the DB with a description (via any LinkedIn channel)."""
    from jobradar.db.models import JobPost
    from jobradar.db.session import SessionLocal

    found: set[str] = set()
    with SessionLocal() as session:
        for i in range(0, len(ids), 500):
            found |= set(
                session.scalars(
                    select(JobPost.external_id).where(
                        JobPost.external_id.in_(ids[i : i + 500]),
                        JobPost.source.in_(LINKEDIN_SOURCES),
                        func.length(func.trim(func.coalesce(JobPost.job_description_full, ""))) > 0,
                    )
                )
            )
    return found


def _url(job_id: str) -> str:
    return f"https://www.linkedin.com/jobs/view/{job_id}/"


@register
class LinkedInPublic(Connector):
    name = "linkedin_jobs_guest"
    description = "LinkedIn Jobs through the public endpoint (no session, free), in the profile's locations"

    def search(self, *, days: int) -> Iterable[Posting]:
        cards: dict[str, dict] = {}
        search = profile.load().search
        for keyword in search.keywords:
            for place, remote in ((loc.name, loc.remote) for loc in search.locations):
                for card in linkedin_public.search_cards(
                    keyword, location=place, date_posted=_window(days), remote=remote, max_results=100
                ):
                    entry = cards.setdefault(card["id"], {**card, "searches": []})
                    entry["searches"].append({"keyword": keyword, "location": place, "remote": remote})
        titles.judge(c.get("title") for c in cards.values())
        candidates = [c for c in cards.values() if filters.title_in_family(c.get("title"))[0]]
        known = _with_description([c["id"] for c in candidates])
        for card in candidates:
            detail_page = None if card["id"] in known else linkedin_public.detail_page(card["id"])
            if card["id"] not in known and not detail_page:
                continue  # the listing does not open: closed or deleted
            self.save_raw({**card, "ficha": detail_page})
            detail_page = detail_page or {}
            yield Posting(
                id=card["id"],
                title=detail_page.get("title") or card.get("title") or "",
                company=detail_page.get("companyName") or card.get("companyName"),
                location=detail_page.get("location") or card.get("location"),
                url=_url(card["id"]),
                description=detail_page.get("description"),
                posted_at=parse_datetime(card.get("postedDate")),
                extra={
                    "searches": card["searches"],
                    "criteria": detail_page.get("criteria"),
                    "applications": detail_page.get("applicationsCount"),
                    "company_url": detail_page.get("companyUrl"),
                },
            )


@register
class LinkedInApify(Connector):
    name = "linkedin_jobs_valig"
    description = (
        "LinkedIn Jobs through the valig actor on Apify (~US$0.0004 per posting), first location of the profile"
    )
    paid = True
    third_party = True  # brings recruiter name and profile
    USD_CAP_PER_SEARCH = 0.15

    def search(self, *, days: int) -> Iterable[Posting]:
        window = _window(days)
        prof = profile.load()
        locations = prof.search.locations
        place = next((loc.name for loc in locations if not loc.remote), prof.location.home_name or locations[0].name)
        for keyword in prof.search.keywords:
            payload = linkedin_apify.build_payload(keyword, location=place, date_posted=window, limit=200)
            run = linkedin_apify.fetch_jobs(payload, max_charge_usd=self.USD_CAP_PER_SEARCH)
            for item in run.items:
                fields = linkedin_apify.job_fields(item)
                if not fields:
                    continue
                yield Posting(
                    id=fields["external_id"],
                    title=fields["title"] or "",
                    company=fields["company"],
                    location=fields["location"],
                    url=fields["post_url"],
                    description=fields["description"],
                    posted_at=fields["posted_at"],
                    extra={"keyword": keyword, "apify_run": run.run_id, "valig": item},
                )


@register
class LinkedInAlerts(Connector):
    name = "linkedin_job_alert_email"
    description = "LinkedIn job alerts arriving in Gmail (description from the public posting page)"
    # These are searches configured by the user: all come in and the fit scorer orders them.
    applies_filters = False

    def search(self, *, days: int) -> Iterable[Posting]:
        from jobradar.gapi import gmail

        svc = gmail.service(gmail.READ_PROFILE)
        query = linkedin_alerts.build_sender_query(linkedin_alerts.DEFAULT_ALERT_SENDERS, newer_than=f"{days}d")
        emails = []
        for mid in gmail.list_message_ids(query, max_results=200, svc=svc):
            message = gmail.get_message(mid, svc=svc)
            payload = message.get("payload") or {}
            jobs = linkedin_alerts.extract_jobs_from_html(
                gmail.part_by_mime(payload, "text/html") or gmail.first_body(payload)
            )
            emails.append((mid, message, jobs))
        known = _with_description([j.job_id for _, _, jobs in emails for j in jobs])
        for mid, message, jobs in emails:
            received = message.get("internalDate")
            meta = {
                "gmail_message_id": mid,
                "email_subject": gmail.header(message, "Subject"),
                "alert_query": query,
                "email_received_at": datetime.fromtimestamp(int(received) / 1000, tz=UTC).isoformat()
                if received
                else None,
            }
            for job in jobs:
                detail_page = (None if job.job_id in known else linkedin_public.detail_page(job.job_id)) or {}
                yield Posting(
                    id=job.job_id,
                    title=detail_page.get("title") or job.title or "",
                    company=detail_page.get("companyName"),
                    location=detail_page.get("location"),
                    url=job.url,
                    description=detail_page.get("description"),
                    extra=meta,
                )
