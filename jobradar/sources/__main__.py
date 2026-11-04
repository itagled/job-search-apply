"""Run job posting connectors and load what they bring.

    python -m jobradar.sources list
    python -m jobradar.sources run remotive getonbrd [--days 7] [--dry-run]
    python -m jobradar.sources run --free [--days 7] [--dry-run]   # those of one field need the profile
    python -m jobradar.sources run glassdoor --paid      # paid ones require explicit permission
    python -m jobradar.sources load --source manual postings.json [...]

``load`` reads a JSON with a list of postings (``id`` and ``title`` required; ``company``,
``location``, ``url``, ``apply_url``, ``description``, ``posted_at``... optional) and passes them
through the same filters and the same deduplication.
"""

from __future__ import annotations

import argparse
import json
import logging
from dataclasses import fields
from pathlib import Path

from sqlalchemy.orm import Session

from jobradar import config, llm, profile
from jobradar.dates import parse_datetime
from jobradar.gapi.auth import MissingCredentialsError
from jobradar.sources.base import REGISTRY, Posting
from jobradar.sources.connectors import load_all
from jobradar.sources.dedupe import Deduplicator
from jobradar.sources.loader import load

log = logging.getLogger("jobradar.sources")
_FIELDS = {f.name for f in fields(Posting)} - {"extra"}


def _posting_from_dict(item: dict) -> Posting:
    data = {k: v for k, v in item.items() if k in _FIELDS}
    data["id"] = str(data.get("id") or "")
    data["title"] = data.get("title") or ""
    data["posted_at"] = parse_datetime(data.get("posted_at"))
    data["countries"] = [c for c in data.get("countries") or [] if c]
    return Posting(**data, extra={k: v for k, v in item.items() if k not in _FIELDS})


def _session() -> Session:
    from jobradar.db.session import SessionLocal

    return SessionLocal()


def cmd_list(_: argparse.Namespace) -> int:
    for name, cls in sorted(REGISTRY.items()):
        only = f" [only with niche_sources: {cls.niche}]" if cls.niche else ""
        print(f"{name:<22} {'paid' if cls.paid else 'free':<5} {cls.description}{only}")
    return 0


def _free_sources(enabled: list[str]) -> list[str]:
    """The free sources; a job board of one field only if the profile lists it."""
    return [n for n, c in sorted(REGISTRY.items()) if not c.paid and (not c.niche or n in enabled)]


def cmd_run(args: argparse.Namespace) -> int:
    enabled = profile.load().search.niche_sources
    llm.require_key()  # job titles are judged by Jev, or by an LLM where there is no Jev
    names = list(args.sources)
    if args.free:
        names += [n for n in _free_sources(enabled) if n not in names]
    unknown = [n for n in names if n not in REGISTRY]
    if unknown or not names:
        log.error("unknown sources: %s; see: jobradar sources list", unknown or "(none requested)")
        return 2
    session = _session()
    try:
        dedupe = Deduplicator.from_db(session)
        for name in names:
            cls = REGISTRY[name]
            if cls.paid and not args.paid:
                print(json.dumps({"source": name, "skipped": "paid API: add --paid"}))
                continue
            try:
                postings = list(cls().search(days=args.days))
                summary = load(
                    name,
                    postings,
                    session=session,
                    dry_run=args.dry_run,
                    dedupe=dedupe,
                    apply_filters=cls.applies_filters,
                )
            except MissingCredentialsError as exc:  # optional: only for those who connected Google
                session.rollback()
                log.warning("source %s skipped: %s", name, exc)
                print(json.dumps({"source": name, "skipped": "no Google credentials"}))
                continue
            except llm.OutOfCreditsError:
                raise
            except Exception as exc:  # a failed source does not cut off the others
                session.rollback()
                log.exception("source %s failed", name)
                print(json.dumps({"source": name, "error": str(exc)[:300]}, ensure_ascii=False))
                continue
            print(json.dumps(summary.as_dict(), ensure_ascii=False))
    finally:
        session.close()
    if llm.usage_total():
        print(json.dumps({"llm": llm.usage_total()}))
    return 0


def cmd_load(args: argparse.Namespace) -> int:
    items = []
    for path in args.files:
        data = json.loads(Path(path).read_text(encoding="utf-8"))
        items += data if isinstance(data, list) else data.get("jobs") or data.get("items") or []
    session = _session()
    try:
        summary = load(
            args.source,
            [_posting_from_dict(i) for i in items if isinstance(i, dict)],
            session=session,
            dry_run=args.dry_run,
        )
    finally:
        session.close()
    print(json.dumps(summary.as_dict(), ensure_ascii=False))
    return 0


def main(argv: list[str] | None = None) -> int:
    load_all()
    ap = argparse.ArgumentParser(description="Job posting sources: download, filter, deduplicate and load")
    sub = ap.add_subparsers(dest="cmd", required=True)
    sub.add_parser("list", help="available connectors").set_defaults(func=cmd_list)
    run = sub.add_parser("run", help="run connectors and load what they bring")
    run.add_argument("sources", nargs="*", help="source names (see 'list')")
    run.add_argument("--free", action="store_true", help="all free sources")
    run.add_argument("--paid", action="store_true", help="allows running paid sources (Apify)")
    run.add_argument("--days", type=int, default=7, help="publication window (default 7)")
    run.add_argument("--dry-run", action="store_true", help="does not write to the DB")
    run.set_defaults(func=cmd_run)
    loader = sub.add_parser("load", help="load a JSON of postings (e.g., hand-built)")
    loader.add_argument("--source", required=True)
    loader.add_argument("files", nargs="+")
    loader.add_argument("--dry-run", action="store_true")
    loader.set_defaults(func=cmd_load)
    args = ap.parse_args(argv)
    try:
        return args.func(args)
    except (profile.ProfileError, config.ConfigError, llm.OutOfCreditsError) as exc:
        log.error("%s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
