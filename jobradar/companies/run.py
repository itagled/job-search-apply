#!/usr/bin/env python3
"""CLI for the target company funnel. Run manually, with human review between stages.

    jobradar companies <stage> [options]

Stages:
    extract       S0  populate companies (Apollo + curated seed)
    screen        S1  cheap heuristic (raw -> screened)
    enrich-base   S2  Apollo/Serper (screened -> base_enriched)
    rescreen      S3  complex heuristic (base_enriched -> rescreened)
    classify      S4  LLM: startup and fit with the profile (rescreened -> classified)
    contacts      S5  people via Apify (classified -> contacts_done)
    export/import-review   human review via xlsx
    stats         summary by stage and tier

Between stages: ``export <stage>`` -> edit ``review_decision`` -> ``import-review``. Only rows
without ``drop`` or ``hold`` advance. For the companies marked ``promote`` there is also a LinkedIn
post monitor: ``backfill-company-linkedin`` -> ``contacts-promoted`` -> ``monitor-posts`` ->
``classify-posts`` -> ``promote-posts`` (and ``posts-report`` to view them).

The code for each stage is in ``jobradar/companies/stages/``; here only the arguments are built.
Progress goes to the log (configured by ``jobradar``) and at the end a JSON summary is printed.
"""

from __future__ import annotations

import argparse
import json
import logging
from collections.abc import Callable
from typing import Any

from jobradar import profile
from jobradar.companies.stages import classify, contacts, enrichment, extract, posts, review, screening
from jobradar.companies.stages.common import TIERS

log = logging.getLogger(__name__)

FUNNEL_STAGES = "raw|screened|base_enriched|rescreened|classified|contacts_done"


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(description="Target company funnel (with human review between stages).")
    sub = p.add_subparsers(dest="command", required=True)

    def stage(
        name: str,
        func: Callable[..., dict[str, Any]],
        help_text: str,
        *,
        limit: bool = True,
        workers: int | None = None,
        top: bool = False,
    ) -> argparse.ArgumentParser:
        """Subcommand ``name`` that calls ``func`` with the options as keyword arguments."""
        sp = sub.add_parser(name, help=help_text, description=help_text)
        sp.set_defaults(func=func)
        if top:
            sp.add_argument("--top", type=int, default=None, help="Only the top N promoted companies")
            sp.add_argument(
                "--order",
                choices=["fit", "employees"],
                default="fit",
                help="Criterion for --top: fit_score (default) or employee_count",
            )
        if limit:
            sp.add_argument("--limit", type=int, default=None, help="Maximum rows to process")
        if workers:
            sp.add_argument("--workers", type=int, default=workers, help=f"Parallel calls (default {workers})")
        return sp

    e = stage("extract", extract.extract, "S0: populate companies", limit=False)
    e.add_argument("--source", choices=extract.SOURCES, default="all")
    e.add_argument("--emp-min", type=int, default=5, help="Minimum employees in Apollo (default 5)")
    e.add_argument("--emp-max", type=int, default=100, help="Maximum employees in Apollo (default 100)")
    e.add_argument(
        "--location",
        default=None,
        help="Location in Apollo (default: the profile's [companies] search_location or country)",
    )
    e.add_argument("--target", type=int, default=None, help="Cap on Apollo companies (default: all)")
    e.add_argument("--dry-run", action="store_true", help="Saves nothing; from Apollo only counts what it would bring")

    stage("screen", screening.screen, "S1: cheap heuristic (raw -> screened)")

    e = stage(
        "enrich-base", enrichment.enrich_base, "S2: Apollo (free) + optional Serper (-> base_enriched)", workers=10
    )
    e.add_argument("--min-tier", choices=TIERS, default=None, help="Only companies with tier_pre from this level")
    e.add_argument(
        "--serper",
        action="store_true",
        help="Use Serper (PAID API) when Apollo does not find the company. Off by default.",
    )

    e = stage(
        "enrich-serp",
        enrichment.enrich_serp,
        "Description via Google Search (Apify, cheap) for the companies without one",
        workers=8,
    )
    e.add_argument("--source", default=None, help="Only companies with this first_source (e.g. curated)")
    e.add_argument(
        "--allow-no-domain", action="store_true", help="Include companies without domain (searches by name + country)"
    )

    stage("rescreen", screening.rescreen, "S3: complex heuristic (base_enriched -> rescreened)")
    stage("classify", classify.classify, "S4: startup and fit with the LLM (-> classified)", workers=8)

    e = stage(
        "contacts",
        contacts.contacts,
        "S5: sales/product/founder people via Apify (-> contacts_done)",
        workers=6,
    )
    e.add_argument("--min-fit", choices=TIERS, default="high", help="Minimum fit (default high)")
    e.add_argument("--min-score", type=int, default=None, help="Minimum fit_score")

    e = stage("export", review.export, "Export to .xlsx for the human review")
    e.add_argument("stage", nargs="?", default=None, help=f"Only this stage ({FUNNEL_STAGES})")
    e.add_argument("--path", default=None, help="Output file (default exports/companies_<stage>.xlsx)")
    e.add_argument("--contacts", action="store_true", help="Export contacts instead of companies")

    e = stage("import-review", review.import_review, "Import the review decisions from an .xlsx", limit=False)
    e.add_argument("--path", required=True)

    stage("stats", review.stats, "Summary by stage and tier", limit=False)

    # LinkedIn post monitor (promoted companies)
    stage(
        "backfill-company-linkedin",
        enrichment.backfill_company_linkedin,
        "Fill in linkedin_url of the promoted companies (Google Search on Apify)",
        workers=8,
        top=True,
    )
    e = stage(
        "contacts-promoted",
        contacts.contacts_promoted,
        "People (talent/founders/sales) of the promoted companies via Apify",
        workers=6,
        top=True,
    )
    e.add_argument("--max-items", type=int, default=25, help="Maximum profiles per company (default 25)")
    e = stage(
        "monitor-posts",
        posts.monitor_posts,
        "Recent posts of the monitored people -> person_post",
        workers=6,
        top=True,
    )
    e.add_argument("--max-posts", type=int, default=25)
    e.add_argument(
        "--posted-limit", default="month", help="Window in Apify: any|24h|week|month|3months|... (default month)"
    )
    stage(
        "classify-posts",
        posts.classify_posts,
        "Classify the pending posts with the LLM (a job offer? which role?)",
        workers=8,
    )
    stage("promote-posts", posts.promote_posts, "Move the target offers to job_post (for fit scoring)", limit=False)
    stage("posts-report", posts.posts_report, "List the target openings found in posts", limit=False)
    return p


def main(argv: list[str] | None = None) -> int:
    args = vars(build_parser().parse_args(argv))
    func = args.pop("func")
    del args["command"]
    try:
        result = func(**args)
    except profile.ProfileError as exc:
        log.error("%s", exc)
        return 1
    print(json.dumps(result, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
