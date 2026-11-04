"""Scraper for LinkedIn profile posts via Apify (harvestapi/linkedin-profile-posts).

Uses ``jobradar.apify.run_sync``: a single call runs the actor and returns the dataset
items, without manual polling. Posts are stored raw as third-party data.

For the "last month posts" case use ``posted_limit="month"`` (Apify
server-side filter) + the client-side filter that discards anything older than ~31 days.
"""

from __future__ import annotations

from datetime import UTC, datetime, timedelta
from typing import Any

from jobradar import apify, config
from jobradar.apify import ApifyAuthError, ApifyError  # noqa: F401 - errors from this module
from jobradar.db.upsert import NormalizedPersonPost, normalize_external_id

DEFAULT_MAX_POSTS = 25
DEFAULT_POSTED_LIMIT = "month"

VALID_POSTED_LIMITS = frozenset({"any", "1h", "24h", "week", "month", "3months", "6months", "year"})
# Apify's postedLimit → max client-side age (only for posts with a date).
POSTED_LIMIT_MAX_AGE: dict[str, timedelta] = {
    "1h": timedelta(hours=1),
    "24h": timedelta(hours=24),
    "week": timedelta(days=7),
    "month": timedelta(days=31),
    "3months": timedelta(days=93),
    "6months": timedelta(days=186),
    "year": timedelta(days=366),
}


def _parse_posted_at(item: dict[str, Any]) -> datetime | None:
    """Apify's ``postedAt`` (object or ISO string) → tz-aware datetime or None."""
    posted = item.get("postedAt")
    if isinstance(posted, dict):
        value = posted.get("date")
        ts = posted.get("timestamp")
        if value:
            try:
                return datetime.fromisoformat(str(value).replace("Z", "+00:00"))
            except ValueError:
                pass
        if ts is not None:
            try:
                return datetime.fromtimestamp(float(ts) / 1000.0, tz=UTC)
            except (TypeError, ValueError, OSError):
                return None
        return None
    if isinstance(posted, str) and posted.strip():
        try:
            return datetime.fromisoformat(posted.replace("Z", "+00:00"))
        except ValueError:
            return None
    return None


def is_post_item(item: dict[str, Any]) -> bool:
    """True if the row is a post (not reaction/comment)."""
    item_type = item.get("type")
    return item_type is None or item_type == "post"


def is_within_posted_limit(
    item: dict[str, Any],
    *,
    max_age: timedelta,
    now: datetime | None = None,
) -> bool:
    """True if the post has no date (we rely on the actor's filter) or is
    within ``max_age``."""
    posted = _parse_posted_at(item)
    if posted is None:
        return True
    ref = now or datetime.now(UTC)
    if posted.tzinfo is None:
        posted = posted.replace(tzinfo=UTC)
    if ref.tzinfo is None:
        ref = ref.replace(tzinfo=UTC)
    return posted >= (ref - max_age)


def map_item_to_person_post(
    item: dict[str, Any],
    *,
    contact_id: int | None = None,
    company_id: int | None = None,
) -> NormalizedPersonPost | None:
    """Map an Apify item to ``NormalizedPersonPost``. None if there is no text."""
    text = item.get("content") or item.get("text")
    if not text or not str(text).strip():
        return None
    post_url = item.get("linkedinUrl") or item.get("postUrl")
    activity_id = item.get("id")
    try:
        external_id = normalize_external_id(
            activity_id=str(activity_id) if activity_id is not None else None,
            post_url=str(post_url) if post_url else None,
        )
    except ValueError:
        return None
    author = item.get("author") if isinstance(item.get("author"), dict) else {}
    author_url = author.get("linkedinUrl")
    return NormalizedPersonPost(
        external_id=external_id,
        post_url=str(post_url) if post_url else None,
        author_linkedin_url=str(author_url) if author_url else None,
        contact_id=contact_id,
        company_id=company_id,
        posted_at=_parse_posted_at(item),
        raw_content=str(text),
        raw_payload=item,
    )


def fetch_profile_posts_for_urls(
    profile_urls: list[str],
    *,
    max_posts: int = DEFAULT_MAX_POSTS,
    posted_limit: str = DEFAULT_POSTED_LIMIT,
    timeout_secs: float = 300.0,
    filter_stale: bool = True,
) -> list[dict[str, Any]]:
    """Run the profile-posts actor for one or more profile URLs.

    Output: list of raw posts (reactions/comments filtered; optionally
    discards those dated older than the window). Raises ``ApifyError`` on failure.
    """
    urls = [u.strip() for u in profile_urls if u and str(u).strip()]
    if not urls:
        return []

    limit_key = (posted_limit or DEFAULT_POSTED_LIMIT).strip().lower()
    if limit_key not in VALID_POSTED_LIMITS:
        raise ValueError(f"invalid posted_limit={posted_limit!r}; expected one of {sorted(VALID_POSTED_LIMITS)}")

    payload: dict[str, Any] = {
        "targetUrls": urls,
        "maxPosts": max_posts,
        "includeQuotePosts": True,
        "includeReposts": False,
        "scrapeReactions": False,
        "scrapeComments": False,
    }
    if limit_key != "any":
        payload["postedLimit"] = limit_key

    items = apify.run_sync(
        config.get("APIFY_LINKEDIN_PROFILE_POSTS_ACTOR") or "",
        payload,
        timeout_secs=max(1, int(timeout_secs)),
        raw_kind="linkedin_profile_posts",
        third_party=True,
    )
    posts = [it for it in items if isinstance(it, dict) and is_post_item(it)]
    max_age = POSTED_LIMIT_MAX_AGE.get(limit_key) if limit_key != "any" else None
    if filter_stale and max_age is not None:
        posts = [it for it in posts if is_within_posted_limit(it, max_age=max_age)]
    return posts
