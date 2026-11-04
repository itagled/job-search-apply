"""Normalization of company names/domains for dedup.

Reuses the style of ``jobradar/db/upsert.py`` (URL canonicalization) but focused
on companies: natural key = normalized domain, with fallback to normalized name.
"""

from __future__ import annotations

import re
from urllib.parse import urlparse

from jobradar.text import strip_accents

_WS_RE = re.compile(r"\s+")
# Legal/corporate suffixes that do not contribute to company identity.
_LEGAL_SUFFIXES = (
    "spa",
    "s.p.a",
    "spa.",
    "ltda",
    "limitada",
    "s.a",
    "sa",
    "s.a.c",
    "sac",
    "inc",
    "inc.",
    "llc",
    "corp",
    "co",
    "company",
    "srl",
    "s.r.l",
    "gmbh",
    "holding",
    "group",
    "grupo",
)

_SOCIAL_HOSTS = frozenset(
    {
        "linkedin.com",
        "www.linkedin.com",
        "cl.linkedin.com",
        "ar.linkedin.com",
        "mx.linkedin.com",
        "br.linkedin.com",
        "facebook.com",
        "www.facebook.com",
        "instagram.com",
        "www.instagram.com",
        "twitter.com",
        "x.com",
        "youtube.com",
        "www.youtube.com",
    }
)


def clean_optional(value: object) -> str | None:
    """Normalize a value to a non-empty str or ``None``.

    Input: any value from a provider (str/None/others).
    Output: stripped non-empty str, or ``None``.
    """
    if isinstance(value, str):
        stripped = value.strip()
        return stripped or None
    return None


def normalize_name(name: str | None) -> str | None:
    """Normalized key of a company name (for dedup by name).

    Input: raw name (``"Acme SpA"``).
    Output: lowercase key without accents, punctuation, or legal suffix
    (``"acme"``), or ``None`` if it becomes empty.
    """
    cleaned = clean_optional(name)
    if not cleaned:
        return None
    text = strip_accents(cleaned)
    text = re.sub(r"[^a-z0-9\s]", " ", text)
    text = _WS_RE.sub(" ", text).strip()
    if not text:
        return None
    tokens = [t for t in text.split(" ") if t and t not in _LEGAL_SUFFIXES]
    result = " ".join(tokens).strip()
    return result or text or None


def is_valid_company_domain(host: str | None) -> bool:
    """True when a host looks like a real company domain (not social media)."""
    if not host:
        return False
    if host in _SOCIAL_HOSTS:
        return False
    if host.endswith(".linkedin.com") or host == "linkedin.com":
        return False
    return "." in host


def normalize_domain(value: str | None) -> str | None:
    """Extract the registrable domain from a URL/host and validate it.

    Input: URL or host (``"https://www.acme.example/cl"``).
    Output: lowercase host without ``www.`` (``"acme.example"``), or ``None`` if
    it is not a plausible company domain.
    """
    cleaned = clean_optional(value)
    if not cleaned:
        return None
    candidate = cleaned if "://" in cleaned else f"https://{cleaned}"
    parsed = urlparse(candidate)
    host = (parsed.netloc or parsed.path.split("/")[0]).lower().strip()
    if host.startswith("www."):
        host = host[4:]
    host = host.split(":")[0]  # remove port if present
    if not is_valid_company_domain(host):
        return None
    return host or None
