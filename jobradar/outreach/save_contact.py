#!/usr/bin/env python3
"""Saves (or updates) a decision maker + their email drafted in ``application_contact``.

Input: JSON (file or stdin) with an object or a list of objects:
    {"job_post_id": 123, "company": "Acme", "company_domain": "acme.example",
     "job_title": "Revenue Operations Manager", "full_name": "...", "first_name": "...",
     "title": "...", "role_type": "hiring_manager|recruiter|founder|exec",
     "linkedin_url": "...", "email": "...", "email_status": "verified|guessed|...",
     "country": "...", "language": "es|en|pt", "source": "apollo|google|...",
     "apollo_id": "...", "raw": {...}, "email_subject": "...", "email_body": "...",
     "cv_file": "profile/cv/....pdf", "outreach_status": "ready|ready_draft|pending|skipped",
     "notes": "..."}

If a row with the same company and email already exists and was NOT sent, it is updated;
if it was already sent (sent) or left as draft (draft), it is not touched.

    jobradar save-contact contacto.json
    echo '{...}' | jobradar save-contact -
"""

from __future__ import annotations

import argparse
import json
import logging
import sys
from pathlib import Path

from sqlalchemy import func, select
from sqlalchemy.orm import Session

from jobradar.db.models import ApplicationContact as C
from jobradar.db.session import SessionLocal

log = logging.getLogger(__name__)

FIELDS = (
    "job_post_id",
    "company",
    "company_domain",
    "job_title",
    "full_name",
    "first_name",
    "title",
    "role_type",
    "linkedin_url",
    "email",
    "email_status",
    "country",
    "language",
    "source",
    "apollo_id",
    "raw",
    "email_subject",
    "email_body",
    "cv_file",
    "outreach_status",
    "notes",
)


def save(s: Session, c: dict) -> str:
    vals = {k: c.get(k) for k in FIELDS}
    vals["raw"] = c.get("raw") or {}
    vals["outreach_status"] = vals["outreach_status"] or "pending"
    if not vals["company"]:
        raise ValueError("missing company")
    if vals["email"]:
        same = func.lower(C.email) == func.lower(vals["email"])
    elif vals["linkedin_url"]:
        same = C.linkedin_url == vals["linkedin_url"]
    else:
        same = None
    existing = None
    if same is not None:
        existing = s.scalars(select(C).where(func.lower(C.company) == func.lower(vals["company"]), same)).first()
    if existing is not None and existing.outreach_status in ("sent", "draft"):
        return f"unchanged (row {existing.id} is already {existing.outreach_status})"
    if existing is not None:
        for k, v in vals.items():
            setattr(existing, k, v)
        s.flush()
        return f"updated row {existing.id}"
    row = C(**vals)
    s.add(row)
    s.flush()
    return f"inserted row {row.id}"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Saves decision makers and their email in application_contact")
    ap.add_argument("file", nargs="?", default="-", help="JSON with an object or a list (- = stdin)")
    args = ap.parse_args(argv)
    raw = sys.stdin.read() if args.file == "-" else Path(args.file).read_text(encoding="utf-8")
    data = json.loads(raw)
    items = data if isinstance(data, list) else [data]
    s = SessionLocal()
    try:
        for c in items:
            log.info("%s / %s <%s>: %s", c.get("company"), c.get("full_name"), c.get("email"), save(s, c))
        s.commit()
    finally:
        s.close()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
