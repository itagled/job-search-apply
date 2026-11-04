"""Common pieces of the stages: which companies stay in the funnel and how they are processed.

Pattern for stages that call an API: ``read`` the work list in a session that is
closed right away, ``process`` each item in parallel and save results in batches, each batch
in its own transaction (``write_with_retry``). The DB connection never stays open
while waiting for an HTTP response.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Iterable, Iterator
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass
from datetime import UTC, datetime
from typing import Any, TypeVar

from sqlalchemy import Select, nullslast, select
from sqlalchemy.orm import Session

from jobradar.db.models import TargetCompany
from jobradar.db.session import get_session, write_with_retry

log = logging.getLogger(__name__)

T = TypeVar("T")
R = TypeVar("R")

TIERS = ("low", "medium", "high")

# Companies that human review did not remove from the funnel (``drop``) nor put on hold (``hold``).
ACTIVE = (
    TargetCompany.review_decision.is_distinct_from("drop"),
    TargetCompany.review_decision.is_distinct_from("hold"),
)
# The best companies according to human review: the LinkedIn monitor only follows these.
PROMOTED = TargetCompany.review_decision == "promote"


def now() -> datetime:
    return datetime.now(UTC)


def tiers_from(minimum: str) -> list[str]:
    """Tiers from ``minimum`` upward: ``"medium"`` → ``["medium", "high"]``."""
    return list(TIERS[TIERS.index(minimum) :])


def ids_top(n: int, order: str = "fit") -> Select:
    """Subquery with the ids of the ``n`` promoted companies with the best ``fit_score``
    (or with the most employees, with ``order="employees"``)."""
    col = TargetCompany.employee_count if order == "employees" else TargetCompany.fit_score
    return select(TargetCompany.id).where(PROMOTED).order_by(nullslast(col.desc())).limit(n)


def read(stmt: Select, limit: int | None = None) -> list[Any]:
    """The rows of ``stmt`` (up to ``limit``), read in a session that closes when done."""
    if limit:
        stmt = stmt.limit(limit)
    with get_session() as session:
        return list(session.execute(stmt).all())


def in_parallel(fn: Callable[[T], R], items: Iterable[T], workers: int, *, warm_up: bool = False) -> Iterator[R]:
    """Apply ``fn`` to each item with ``workers`` threads and deliver results as they finish.

    With ``warm_up``, the first item goes alone: the LLM provider caches the system prompt and
    parallel calls take advantage of it. If something interrupts the cycle (an error, no balance),
    calls that did not get to start are cancelled.
    """
    pending = list(items)
    if warm_up and pending:
        yield fn(pending.pop(0))
    if workers <= 1:
        yield from map(fn, pending)
        return
    ex = ThreadPoolExecutor(max_workers=workers)
    try:
        for fut in as_completed([ex.submit(fn, item) for item in pending]):
            yield fut.result()
    finally:
        ex.shutdown(wait=True, cancel_futures=True)


class Batch:
    """Accumulate rows and write them in batches of ``size``, each batch in its own transaction with retry.

    On exiting the ``with``, write whatever remains, also if there was an error halfway: what
    was already paid for (API or LLM) is not lost.
    """

    def __init__(self, write: Callable[[Session, list[Any]], None], size: int = 50) -> None:
        self._write = write
        self._size = size
        self._rows: list[Any] = []

    def add(self, row: Any) -> None:
        self._rows.append(row)
        if len(self._rows) >= self._size:
            self.flush()

    def flush(self) -> None:
        if self._rows:
            rows, self._rows = self._rows, []
            write_with_retry(lambda session: self._write(session, rows))

    def __enter__(self) -> Batch:
        return self

    def __exit__(self, *exc: object) -> None:
        self.flush()


@dataclass
class Failure:
    """What ``fn`` returns in ``process`` when an item fails: it stays for the next pass."""

    reason: str


def process(
    name: str,
    work: list[T],
    fn: Callable[[T], Any],
    save: Callable[[Session, list[Any]], None],
    workers: int,
    *,
    warm_up: bool = False,
) -> dict[str, int]:
    """Apply ``fn`` to each item in parallel and save in batches what it returns.

    ``fn`` does the slow part (the API or the LLM) and returns the row to save, ``None`` if there is nothing
    to save (the API did not find the data) or a ``Failure``. The first 5 failures are reported.
    """
    done = saved = failed = 0
    with Batch(save) as batch:
        for row in in_parallel(fn, work, workers, warm_up=warm_up):
            done += 1
            if isinstance(row, Failure):
                failed += 1
                if failed <= 5:
                    log.warning("%s: %s", name, row.reason)
            elif row is not None:
                saved += 1
                batch.add(row)
            if done % 200 == 0:
                log.info("%s: %d/%d", name, done, len(work))
    return {"processed": done, "saved": saved, "failed": failed}
