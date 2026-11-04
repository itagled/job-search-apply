#!/usr/bin/env python3
"""Watches applications that are waiting for a step from the user and notifies (one line per novelty).

Prints a line when a new ``application_pending`` appears in job_post
(captcha, account, test, login...). Intended to run with the Claude Monitor:
each line wakes the session to notify the user immediately.

    jobradar watch-pending [--interval 45]
"""

from __future__ import annotations

import argparse
import json
import logging
import time

from jobradar import config
from jobradar.db import applications
from jobradar.db.session import SessionLocal

log = logging.getLogger(__name__)

SEEN_FILE = config.DATA_DIR / "outreach" / "pending_announced.json"


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--interval", type=float, default=45.0)
    args = ap.parse_args(argv)
    seen: set[int] = set(json.loads(SEEN_FILE.read_text())) if SEEN_FILE.is_file() else set()
    while True:
        try:
            with SessionLocal() as s:
                rows = applications.incomplete_applications(s, one_per_link=False)
        except Exception as exc:  # noqa: BLE001 - the DB may drop; it retries
            log.error("watch_pending: DB error %s", type(exc).__name__)
            time.sleep(args.interval)
            continue
        for r in rows:
            if r["id"] in seen:
                continue
            seen.add(r["id"])
            SEEN_FILE.parent.mkdir(parents=True, exist_ok=True)
            SEEN_FILE.write_text(json.dumps(sorted(seen)))
            log.warning(
                "PENDING | %s | %s | reason: %s | %s | %s",
                r["company"],
                r["title"],
                r["reason"],
                r["url"],
                (r["note"] or "")[:200],
            )
        time.sleep(args.interval)


if __name__ == "__main__":
    raise SystemExit(main())
