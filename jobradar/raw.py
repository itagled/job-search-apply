"""Raw storage of what comes from paid APIs: as soon as it arrives, complete and in batches.

Each batch goes to ``data/raw/<type>/<YYYY-MM-DD>.jsonl``, one line per record, with the save
date and query parameters. Thus, if something fails midway, what was already paid stays on disk
and can be reprocessed without paying again.

Third-party personal data (Law 21.719): the caller minimizes it BEFORE saving and uses
``third_party=True``, which places the batch under ``data/raw/third_party/`` with a deletion deadline
(12 months by default)::

    python -m jobradar.raw --purge
"""

from __future__ import annotations

import argparse
import json
import threading
from collections.abc import Iterable
from datetime import UTC, date, datetime, timedelta
from pathlib import Path
from typing import Any

from jobradar import config

RAW_DIR = config.DATA_DIR / "raw"
THIRD_PARTY_DIR = RAW_DIR / "third_party"
THIRD_PARTY_RETENTION_DAYS = 365

_lock = threading.Lock()


def append(kind: str, records: Iterable[Any], *, third_party: bool = False, **meta: Any) -> Path:
    """Add records to the JSONL of the day for ``kind``. ``meta`` goes in each line (source, parameters)."""
    base = THIRD_PARTY_DIR if third_party else RAW_DIR
    path = base / kind / f"{date.today().isoformat()}.jsonl"
    saved_at = datetime.now(UTC).isoformat(timespec="seconds")
    lines = [
        json.dumps({"saved_at": saved_at, **meta, "data": rec}, ensure_ascii=False, default=str) for rec in records
    ]
    if lines:
        path.parent.mkdir(parents=True, exist_ok=True)
        with _lock, path.open("a", encoding="utf-8") as fh:
            fh.write("\n".join(lines) + "\n")
    return path


def purge(retention_days: int = THIRD_PARTY_RETENTION_DAYS, *, today: date | None = None) -> list[Path]:
    """Delete batches with third-party data older than the deadline. Returns what was deleted."""
    limit = (today or date.today()) - timedelta(days=retention_days)
    removed = []
    for path in sorted(THIRD_PARTY_DIR.glob("*/*.jsonl")):
        try:
            day = date.fromisoformat(path.stem)
        except ValueError:
            continue
        if day < limit:
            path.unlink()
            removed.append(path)
    return removed


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Raw from APIs: delete expired third-party data")
    ap.add_argument("--purge", action="store_true", help="delete out-of-deadline third-party batches")
    ap.add_argument("--days", type=int, default=THIRD_PARTY_RETENTION_DAYS, help="deadline in days (default 365)")
    args = ap.parse_args(argv)
    if args.purge:
        removed = purge(args.days)
        print(json.dumps({"deleted": len(removed)}))
    else:
        ap.print_help()
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
