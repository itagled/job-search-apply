"""Shared text helpers (normalize, compare, extract domains)."""

from __future__ import annotations

import html
import re
import unicodedata
from urllib.parse import urlparse


def strip_accents(text: str | None) -> str:
    """Lowercase and without accents, to search keywords regardless of accents or case."""
    if not text:
        return ""
    nfkd = unicodedata.normalize("NFKD", str(text))
    return "".join(c for c in nfkd if not unicodedata.combining(c)).lower().strip()


def normalize(text: str | None) -> str:
    """``strip_accents`` and also without punctuation and with collapsed spaces (to compare names)."""
    cleaned = "".join(c if c.isalnum() or c.isspace() else " " for c in strip_accents(text))
    return " ".join(cleaned.split())


def host(url: str | None) -> str:
    """Host in lowercase and without ``www.`` (accepts URLs without scheme); ``""`` if none."""
    if not url:
        return ""
    try:
        netloc = urlparse(url if "://" in url else f"https://{url}").netloc.lower()
    except ValueError:
        return ""
    return netloc[4:] if netloc.startswith("www.") else netloc


def strip_html(value: str | None) -> str:
    """Readable text from HTML: no scripts or tags, with line breaks in paragraphs and items."""
    if not value:
        return ""
    text = html.unescape(value)
    text = re.sub(r"(?is)<(script|style)[^>]*>.*?</\1>", " ", text)
    text = re.sub(r"(?i)<br\s*/?>|</p>|</li>|</h\d>|</div>", "\n", text)
    text = re.sub(r"(?i)<li[^>]*>", "- ", text)
    text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = re.sub(r"[ \t\xa0]+", " ", text)
    text = re.sub(r"\n\s*\n+", "\n\n", text)
    return text.strip()
