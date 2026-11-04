"""ATS boards of well-known companies (Ashby, Greenhouse, Lever, Workable): their published job posts.

The list of boards comes from the DB (companies from which a job post already entered through that ATS) and,
optionally, from ``data/sources/ats_boards.txt`` (one line ``ats:board`` per new company).
"""

from __future__ import annotations

from collections.abc import Iterable
from datetime import UTC, datetime, timedelta
from typing import ClassVar

from sqlalchemy import select

from jobradar import config
from jobradar.sources import ats, filters, titles
from jobradar.sources.base import Connector, Posting, register


class _BoardsAts(Connector):
    ats: ClassVar[str]

    def boards(self) -> list[str]:
        from jobradar.db.models import JobPost
        from jobradar.db.session import SessionLocal

        with SessionLocal() as session:
            board = JobPost.source_metadata[self.name]["board"].as_string()
            boards = {b for b in session.scalars(select(board).where(JobPost.source == self.name).distinct()) if b}
        extra = config.DATA_DIR / "sources" / "ats_boards.txt"
        if extra.is_file():
            for line in extra.read_text(encoding="utf-8").splitlines():
                ats_name, _, slug = line.strip().partition(":")
                if ats_name == self.ats and slug:
                    boards.add(slug)
        return sorted(boards)

    def search(self, *, days: int) -> Iterable[Posting]:
        limit = datetime.now(UTC) - timedelta(days=days)
        recent: list[Posting] = []
        for board in self.boards():
            postings = ats.list_board(self.ats, board)
            self.save_raw([a.metadata() for a in postings], board=board)
            recent += [a for a in postings if not a.posted_at or a.posted_at >= limit]
        titles.judge(a.title for a in recent)
        for posting in recent:
            if not filters.title_in_family(posting.title)[0]:
                continue
            if not posting.description and not ats.fill_in(posting):
                continue  # the ATS no longer publishes it
            yield posting


for _ats in ("ashby", "greenhouse", "lever", "workable"):
    register(
        type(
            f"Ats{_ats.capitalize()}",
            (_BoardsAts,),
            {
                "__module__": __name__,
                "name": f"ats_{_ats}",
                "ats": _ats,
                "description": f"{_ats.capitalize()}: postings from the boards of known companies (public API)",
            },
        )
    )
