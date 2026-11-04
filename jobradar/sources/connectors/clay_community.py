"""Clay community (Tightknit platform): the channels where job offers are published.

It walks the public sitemap (posts carry a modification date) and reads the JSON-LD of
each post. It respects the site's 3 s ``Crawl-delay`` and stores already-read posts in a
30-day cache. From each post, only the author of the offer is kept (no comments or likes).
"""

from __future__ import annotations

import html
import json
import re
from collections.abc import Iterable
from datetime import UTC, datetime, timedelta

from jobradar import config, http_client
from jobradar.dates import parse_datetime
from jobradar.sources.base import Connector, Posting, register

BASE = "https://community.clay.com"
CHANNELS = ("share-jobs", "part-time-jobs")
PAUSE = 3.0  # Site's Crawl-delay
_URL = re.compile(r"https?://[^\s<>\"')\]]+")


@register
class ClayCommunity(Connector):
    name = "web_clay_community"
    niche = "GTM, growth and RevOps roles"
    description = "Clay community (Tightknit): share-jobs and part-time-jobs channels"
    third_party = True  # the post carries the name of the author

    def _text(self, url: str) -> str:
        cache = http_client.DiskCache(config.DATA_DIR / "cache" / "comunidad_clay", ttl=30 * 86400)
        return http_client.cached_get(url, cache=cache, min_interval=PAUSE, timeout=40).text

    def search(self, *, days: int) -> Iterable[Posting]:
        since = (datetime.now(UTC) - timedelta(days=days)).date().isoformat()
        indice = self._text(BASE + "/server-sitemap-index.xml")
        for mapping in [m for m in re.findall(r"<loc>([^<]+)</loc>", indice) if "sitemap-posts" in m]:
            for url, lastmod in re.findall(
                r"<url><loc>([^<]+)</loc>(?:<lastmod>([^<]+)</lastmod>)?", self._text(mapping)
            ):
                match = re.search(r"/x/([^/]+)/(msg_[A-Za-z0-9]+)", url)
                if not match or match.group(1) not in CHANNELS or (lastmod or "9999")[:10] < since:
                    continue
                post = _post(self._text(url))
                if not post or (post.get("date") or "")[:10] < since:
                    continue
                self.save_raw({"url": url, "channel": match.group(1), **post})
                posting = _posting(url, match.group(2), match.group(1), post)
                if posting:
                    yield posting


def _post(page: str) -> dict:
    """The page's ``DiscussionForumPosting``, only with the author (no comments or likes)."""
    for match in re.finditer(r"<script[^>]*application/ld\+json[^>]*>(.*?)</script>", page or "", re.S):
        try:
            data = json.loads(match.group(1))
        except ValueError:
            continue
        if isinstance(data, dict) and data.get("@type") == "DiscussionForumPosting":
            author = data.get("author") if isinstance(data.get("author"), dict) else {}
            return {
                "date": data.get("datePublished"),
                "title": data.get("headline"),
                "text": data.get("text") or data.get("articleBody"),
                "author": author.get("name"),
                "author_url": author.get("url"),
            }
    return {}


def _field(text: str, labels: str, length: str) -> str | None:
    match = re.search(rf"(?im)^\W*(?:{labels})\W*[:\-]\s*(.{{{length}}})$", text)
    return match.group(1).strip() if match else None


def _posting(url: str, msg_id: str, channel: str, post: dict) -> Posting | None:
    text = html.unescape(post.get("text") or "")
    title = (post.get("title") or "").strip()
    if not text and not title:
        return None
    external = [u.rstrip(".,;") for u in _URL.findall(text) if "community.clay.com" not in u]
    location = _field(text, r"location|ubicaci[oó]n|where", "3,120")
    if not location and re.search(r"(?i)\bremote\b", text[:1500] + title):
        location = "Remote"
    return Posting(
        id=f"clay_community:{msg_id}",
        title=title,
        company=_field(text, r"company|agency|startup|empresa", "2,80"),
        location=location,
        url=url,
        apply_url=external[0] if external else url,
        description=text,
        posted_at=parse_datetime(post.get("date")),
        remote=True if (location or "").lower().startswith("remote") else None,
        extra={"channel": channel, "poster": post.get("author"), "poster_url": post.get("author_url")},
    )
