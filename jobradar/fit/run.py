#!/usr/bin/env python3
"""Fit-scoring batch: scores the job postings in the DB against the target profile and the CV.

Selects rows with ``fit_status='pending'`` that already have a description, scores
them with the LLM, and saves ``fit_score/fit_tier/fit_json``. Rows without a description
are left pending (waiting for the content fetch).

Pre-filter with Jev (``--triage jev``, default; see ``jobradar/fit/jev.py``), in two passes:
1) Jev scores the ENTIRE batch (cheap, ~1 s per posting, ``--jev-workers`` in parallel); rows
   below ``--triage-min`` (25) are saved immediately with that score
   (``fit_json.approach='jev_triage'``, ``triaged_out=true``) and do not go to the LLM.
2) The LLM (``--workers`` in parallel) scores only those that pass (or if Jev failed); it sets
   the final score and saves Jev's in ``fit_json.jev_triage``.
``--triage none`` sends everything to the LLM, and so does a provider without Jev (OpenRouter).

Usage:  jobradar fit [--limit N] [--workers 12] [--jev-workers 16] [--triage jev|none] [--triage-min 25]
Exit: 0 ok · 0 soft-skip if there is no LLM key.
"""

from __future__ import annotations

import argparse
import json
import logging
import time
from collections.abc import Callable, Iterator
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from sqlalchemy import func, select, update
from sqlalchemy.exc import OperationalError

from jobradar import config, llm, profile
from jobradar.db.models import JobPost
from jobradar.db.session import SessionLocal
from jobradar.fit.jev import JevResult

log = logging.getLogger(__name__)

_CV_PATH = config.ROOT / "profile" / "cv.md"


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    ap = argparse.ArgumentParser(description="Fit-scoring of vacancies with the LLM")
    ap.add_argument("--limit", type=int, default=100)
    ap.add_argument("--workers", type=int, default=1, help="Parallel LLM calls (default 1)")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument(
        "--out",
        default="",
        help="Test mode: score and dump to this local JSON instead of writing the DB",
    )
    ap.add_argument(
        "--ids",
        default="",
        help="Comma-separated external_ids to score a one-off sample",
    )
    ap.add_argument(
        "--shard",
        default="",
        help="Partition 'k/n' (e.g. 0/4): only takes rows with id %% n = k, to run n processes in parallel without repeating",
    )
    ap.add_argument(
        "--triage",
        default=config.get("FIT_TRIAGE"),
        choices=["jev", "none"],
        help="Cheap pre-filter before the LLM (default jev, NanoGPT only; env FIT_TRIAGE)",
    )
    ap.add_argument(
        "--jev-workers",
        type=int,
        default=16,
        help="Parallel Jev calls in the 1st pass (default 16)",
    )
    ap.add_argument(
        "--triage-min",
        type=float,
        default=None,
        help="Minimum Jev score to pass to the LLM (default 25; env FIT_TRIAGE_MIN)",
    )
    return ap.parse_args(argv)


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    try:
        if not llm.has_key():
            log.warning("no LLM key (NANOGPT_API_KEY or OPENROUTER_API_KEY): fit scoring skipped")
            return 0
        if args.triage == "jev" and not llm.has_jev():
            log.info("Jev is only on NanoGPT: every posting goes to the LLM")
            args.triage = "none"
    except config.ConfigError as exc:
        log.error("%s", exc)
        return 1
    if not _CV_PATH.is_file():
        log.error("CV not found at %s (copy profile/cv.example.md)", _CV_PATH)
        return 1
    cv = _CV_PATH.read_text(encoding="utf-8")
    try:
        prof = profile.load()
    except profile.ProfileError as exc:
        log.error("%s", exc)
        return 1

    shard_k, shard_n = 0, 1
    if args.shard.strip():
        shard_k, shard_n = (int(x) for x in args.shard.split("/"))

    pers = SessionLocal()
    try:
        # Only postings with a REAL description (not the email title).
        stmt = select(
            JobPost.id,
            JobPost.external_id,
            JobPost.title,
            JobPost.company,
            JobPost.location,
            JobPost.post_url,
            JobPost.raw_content,
            JobPost.job_description_full,
        ).where(func.trim(JobPost.job_description_full) != "")
        if args.ids.strip():  # one-off sample
            stmt = stmt.where(JobPost.external_id.in_([x.strip() for x in args.ids.split(",") if x.strip()]))
        else:  # pending ones, and the failed ones to retry
            stmt = stmt.where(JobPost.fit_status.in_(("pending", "error")))
            if shard_n > 1:
                stmt = stmt.where(JobPost.id % shard_n == shard_k)
            stmt = stmt.order_by(JobPost.created_at.desc()).limit(args.limit)
        rows = pers.execute(stmt).mappings().all()

        if args.dry_run:
            print(json.dumps({"to_score": len(rows), "dry_run": True}))
            return 0
        if not rows:
            print(json.dumps({"scored": 0, "note": "nothing to score"}))
            return 0

        from concurrent.futures import ThreadPoolExecutor

        from jobradar.fit.jev import jev_score, min_score
        from jobradar.fit.scorer import FitResult, _tier, score_job

        triage_min = args.triage_min if args.triage_min is not None else min_score()

        def _pmap(fn: Callable[[Any], Any], items: list[Any], workers: int) -> Iterator[Any]:
            """map over threads (calls are I/O-bound); preserves order."""
            if workers and workers > 1 and len(items) > 1:
                with ThreadPoolExecutor(max_workers=workers) as ex:
                    yield from ex.map(fn, items)
            else:
                yield from map(fn, items)

        def _llm(pair: tuple[Any, JevResult | None]) -> tuple[Any, FitResult]:
            row, jr = pair
            result = score_job(cv, prof.description, dict(row), prof=prof)
            if jr is not None and result.fit_json:
                result.fit_json["jev_triage"] = jr.fit_json if jr.final is not None else {"error": jr.error}
            return row, result

        def _results() -> Iterator[tuple[Any, FitResult]]:
            """Pass 1: Jev over EVERYTHING (fast, highly parallel) and the discarded ones are
            delivered immediately. Pass 2: the LLM only over those that pass the threshold (or if Jev failed)."""
            jevs = {}
            if args.triage == "jev":
                jevs = dict(
                    zip(
                        [r["id"] for r in rows],
                        _pmap(lambda r: jev_score(dict(r), prof=prof), rows, args.jev_workers),
                        strict=True,
                    )
                )
            passed = []
            for r in rows:
                jr = jevs.get(r["id"])
                if jr is not None and jr.final is not None and jr.final < triage_min:
                    score = round(jr.final)
                    yield (
                        r,
                        FitResult(score, _tier(score), {**jr.fit_json, "triaged_out": True, "triage_min": triage_min}),
                    )
                else:
                    passed.append((r, jr))
            yield from _pmap(_llm, passed, args.workers)

        # Test mode: dumps to JSON, does not touch the DB.
        if args.out.strip():
            report = []
            for r, result in _results():
                dims = {k: v.get("score") for k, v in (result.fit_json.get("dimensions") or {}).items()}
                report.append(
                    {
                        "external_id": r["external_id"],
                        "title": r["title"],
                        "company": r["company"],
                        "location": r["location"],
                        "fit_score": result.fit_score,
                        "fit_tier": result.fit_tier,
                        "dimensions": dims,
                        "summary": (result.fit_json.get("summary") if result.fit_json else None),
                        "red_flags": (result.fit_json.get("red_flags") if result.fit_json else None),
                        "error": result.error,
                    }
                )
            report.sort(key=lambda x: (x["fit_score"] is None, -(x["fit_score"] or 0)))
            out_path = Path(args.out)
            out_path.parent.mkdir(parents=True, exist_ok=True)
            out_path.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
            print(json.dumps({"scored_to_file": len(report), "out": str(out_path)}, ensure_ascii=False))
            return 0

        scored = errors = triaged = 0

        def _persist(row: Any, result: FitResult) -> bool:
            """Writes the result (without commit). True if it was an error."""
            ts = datetime.now(UTC)
            this = update(JobPost).where(JobPost.id == row["id"])
            if result.error and result.fit_score is None:
                pers.execute(this.values(fit_status="error", fit_error=result.error[:2000], fit_scored_at=ts))
                return True
            pers.execute(
                this.values(
                    fit_score=result.fit_score,
                    fit_tier=result.fit_tier,
                    fit_json=result.fit_json,
                    fit_status="done",
                    fit_scored_at=ts,
                    company=func.coalesce(func.nullif(func.trim(JobPost.company), ""), result.company_name),
                    fit_error=None,
                )
            )
            return False

        def _save(row: Any, result: FitResult) -> None:
            """Commit per row; if the DB connection drops, reconnect and retry."""
            nonlocal scored, errors, triaged
            for attempt in range(3):
                try:
                    is_error = _persist(row, result)
                    pers.commit()
                    break
                except OperationalError:
                    pers.rollback()
                    if attempt == 2:
                        raise
                    time.sleep(3)
            if is_error:
                errors += 1
            else:
                scored += 1
                triaged += bool((result.fit_json or {}).get("triaged_out"))

        # The calls (Jev and LLM) run in threads; DB writes are done in the
        # main thread (the SQLAlchemy session is not thread-safe).
        out_of_credits = None
        try:
            for row, result in _results():
                _save(row, result)
        except llm.OutOfCreditsError as exc:  # what is already saved remains; resume with the same command
            out_of_credits = str(exc)

        print(
            json.dumps(
                {
                    "scored": scored,
                    "errors": errors,
                    "triage": args.triage,
                    "triaged_out_by_jev": triaged,
                    "llm_scored": scored - triaged,
                    "llm": llm.usage_total(),
                    **({"error": out_of_credits} if out_of_credits else {}),
                },
                ensure_ascii=False,
            )
        )
        return 3 if out_of_credits else 0
    finally:
        pers.close()


if __name__ == "__main__":
    raise SystemExit(main())
