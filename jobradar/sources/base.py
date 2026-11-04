"""Contract for job posting sources: the common ``Posting`` and the base class ``Connector``.

Each connector fetches postings from a portal (API, page, or Apify) and returns them as ``Posting``.
Filtering, deduplication, and saving are the same for all (``jobradar.sources.loader``).
"""

from __future__ import annotations

from collections.abc import Iterable
from dataclasses import asdict, dataclass, field
from datetime import datetime
from typing import Any, ClassVar

from jobradar import raw


@dataclass
class Posting:
    id: str  # stable id within the source (external_id in job_post)
    title: str
    company: str | None = None
    location: str | None = None
    url: str | None = None  # link to the posting
    apply_url: str | None = None  # link to apply, if different
    description: str | None = None
    posted_at: datetime | None = None
    countries: list[str] = field(default_factory=list)  # ISO codes, if the source provides them
    remote: bool | None = None
    salary: str | None = None
    extra: dict[str, Any] = field(default_factory=dict)  # source-specific fields

    def metadata(self) -> dict[str, Any]:
        """The complete posting for ``source_metadata`` (nothing is discarded)."""
        data = asdict(self)
        data["posted_at"] = self.posted_at.isoformat() if self.posted_at else None
        extra = data.pop("extra")
        return {**data, **extra}


class Connector:
    """A posting source. ``name`` is the ``source`` used in ``job_post``."""

    name: ClassVar[str]
    description: ClassVar[str] = ""
    paid: ClassVar[bool] = False  # uses a paid API (Apify): only runs with explicit permission
    third_party: ClassVar[bool] = False  # the raw data contains personal data (recruiters, post authors)
    applies_filters: ClassVar[bool] = True  # False: all are included (e.g., alerts chosen by the user)
    niche: ClassVar[str] = ""  # who a job board of one field is for: off unless the profile lists it

    def search(self, *, days: int) -> Iterable[Posting]:
        """Postings published (or seen) in the last ``days``."""
        raise NotImplementedError

    def save_raw(self, data: Any, **meta: Any) -> None:
        """Save the response as it arrives, immediately (one batch per call)."""
        raw.append(f"sources/{self.name}", [data], third_party=self.third_party, **meta)


REGISTRY: dict[str, type[Connector]] = {}


def register(cls: type[Connector]) -> type[Connector]:
    """Decorator: makes the connector available for ``python -m jobradar.sources``."""
    REGISTRY[cls.name] = cls
    return cls
