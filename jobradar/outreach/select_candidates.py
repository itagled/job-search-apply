#!/usr/bin/env python3
"""Select vacancies to apply to: high score, eligible, title not excluded by the profile, one per company.

jobradar decision-makers --min-score 55 [--out data/outreach/to_apply.json]
"""

from __future__ import annotations

import argparse
import json
import logging
import re
from datetime import UTC, datetime, timedelta
from pathlib import Path

from sqlalchemy import func, or_, select

from jobradar import config, profile
from jobradar.companies.normalize import normalize_name
from jobradar.db.models import JobPost
from jobradar.db.session import SessionLocal

log = logging.getLogger(__name__)

SPANISH_HINT = re.compile(
    r"\b(buscamos|requisitos|experiencia|responsabilidades|ofrecemos|empresa|conocimientos|años)\b", re.I
)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--min-score", type=int, default=55)
    ap.add_argument("--out", default="", help="output JSON (default data/outreach/to_apply.json)")
    ap.add_argument("--max-age-days", type=int, default=60)
    args = ap.parse_args(argv)

    try:
        exclude = profile.load().outreach_exclude
    except profile.ProfileError as exc:
        log.error("%s", exc)
        return 1
    cutoff = datetime.now(UTC) - timedelta(days=args.max_age_days)
    with SessionLocal() as s:
        applied_companies = {
            normalize_name(name) for name in s.scalars(select(JobPost.company).where(JobPost.status == "applied"))
        }
        rows = (
            s.execute(
                select(
                    JobPost.id,
                    JobPost.source,
                    JobPost.external_id,
                    JobPost.title,
                    JobPost.company,
                    JobPost.location,
                    JobPost.post_url,
                    JobPost.fit_score,
                    JobPost.fit_json,
                    JobPost.posted_at,
                    JobPost.source_metadata,
                    func.substr(func.coalesce(JobPost.job_description_full, ""), 1, 3000).label("jd"),
                )
                .where(
                    JobPost.fit_score >= args.min_score,
                    JobPost.status.not_in(("applied", "discarded")),
                    ~JobPost.has_metadata("application_pending"),
                    or_(JobPost.posted_at.is_(None), JobPost.posted_at > cutoff),
                )
                .order_by(JobPost.fit_score.desc())
            )
            .mappings()
            .all()
        )

    picked: dict[str, dict] = {}
    skipped = {"title_excluded": 0, "not_eligible": 0, "company_already_applied": 0, "duplicate_company": 0}
    for r in rows:
        fj = r["fit_json"] or {}
        if exclude and exclude.search(r["title"] or ""):
            skipped["title_excluded"] += 1
            continue
        if fj.get("eligible") == "no":
            skipped["not_eligible"] += 1
            continue
        comp = normalize_name(r["company"]) or normalize_name(r["title"])
        if comp in applied_companies:
            skipped["company_already_applied"] += 1
            continue
        if comp in picked:
            skipped["duplicate_company"] += 1
            continue
        md = r["source_metadata"] or {}
        src_md = md.get(r["source"]) if isinstance(md.get(r["source"]), dict) else {}
        apply_url = (src_md or {}).get("apply_url") or (src_md or {}).get("applyUrl") or r["post_url"]
        picked[comp] = {
            "job_post_id": r["id"],
            "source": r["source"],
            "external_id": r["external_id"],
            "title": r["title"],
            "company": r["company"],
            "location": r["location"],
            "url": r["post_url"],
            "apply_url": apply_url,
            "fit_score": r["fit_score"],
            "eligible": fj.get("eligible"),
            "posted_at": str(r["posted_at"] or "")[:10],
            "jd_language": "es" if len(SPANISH_HINT.findall(r["jd"] or "")) >= 4 else "en",
            "summary": (fj.get("summary") or "")[:300],
        }
    out = list(picked.values())
    path = Path(args.out) if args.out else config.DATA_DIR / "outreach" / "to_apply.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(out, ensure_ascii=False, indent=1), encoding="utf-8")
    print(json.dumps({"candidates": len(out), "discarded": skipped, "file": str(path)}, ensure_ascii=False))
    for c in out:
        log.info(
            "%3s %-7s | %-26s | %-55s | %s",
            c["fit_score"],
            str(c["eligible"]),
            (c["company"] or "")[:26],
            (c["title"] or "")[:55],
            c["source"],
        )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
