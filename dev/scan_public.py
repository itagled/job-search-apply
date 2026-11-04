#!/usr/bin/env python3
"""Privacy scan of a folder before publishing it: personal data, credentials and forbidden files.

    python dev/scan_public.py --path .
    python dev/scan_public.py --path ../copy --denylist secrets/public_denylist.txt

Prints ``file:line: kind``, never the text found, and exits with 1 if there is any finding. The
optional denylist has one private term per line (a name, an email, a phone); terms match whole words
in any case, and numbers match whatever the separators.
"""

from __future__ import annotations

import argparse
import re
import sys
import unicodedata
from collections import Counter
from collections.abc import Iterator
from pathlib import Path

PATTERNS = {
    "email": re.compile(r"[A-Za-z0-9._%+-]+@[A-Za-z0-9-]+\.[A-Za-z.]{2,}"),
    "phone": re.compile(r"\+\d[\d ()-]{7,}\d"),
    "national_id": re.compile(r"\b\d{1,2}\.\d{3}\.\d{3}-[\dkK]\b"),
    "linkedin_profile": re.compile(r"linkedin\.com/in/[A-Za-z0-9%_-]{3,}"),
    "google_id": re.compile(r"[\"']([A-Za-z0-9_-]{28,44})[\"']"),
    "api_key": re.compile(
        r"sk-[A-Za-z0-9_-]{16,}|AIza[0-9A-Za-z_-]{20,}|GOCSPX-|ghp_[A-Za-z0-9]{16,}|github_pat_|apify_api_|"
        r"ya29\.|1//0[A-Za-z0-9_-]{16,}|\bre_[A-Za-z0-9]{16,}|eyJ[A-Za-z0-9_-]{16,}"
    ),
    "database_url": re.compile(r"postgres(?:ql)?(?:\+\w+)?://[^\s'\"@]+:[^\s'\"@]+@[^\s'\"]+"),
    "windows_path": re.compile(r"[A-Za-z]:[\\/]Users[\\/]"),
}
# Fictional values the code and the tests may use.
ALLOWED = {
    "email": re.compile(r"@example\.(com|org|net)$|\.example$|^onboarding@resend\.dev$|@linkedin\.com$"),
    "phone": re.compile(r"1234|2345"),
    "linkedin_profile": re.compile(r"example"),
    "database_url": re.compile(r"@host\b"),
}
FORBIDDEN_FILE = re.compile(
    r"\.(pdf|docx|xlsx|zip|csv|jsonl|png|jpe?g|svg|ttf)$|(^|/)token[^/]*\.json$|(^|/)credentials?[^/]*\.json$", re.I
)
FORBIDDEN_DIRS = {"data", "secrets", "exports"}  # never in a copy
SKIPPED_DIRS = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", ".ruff_cache", "build", "dist"}
MAX_SIZE = 1_000_000
WORD = re.compile(r"[^\W_]+")


def _words(text: str) -> list[str]:
    plain = "".join(c for c in unicodedata.normalize("NFD", text) if not unicodedata.combining(c))
    return WORD.findall(plain.lower())


def load_denylist(path: Path | None) -> set[str]:
    """The terms of the denylist, as lowercase words without accents joined by one space."""
    if path is None:
        return set()
    lines = path.read_text(encoding="utf-8").splitlines()
    return {" ".join(_words(line)) for line in lines if line.strip() and not line.startswith("#")} - {""}


def _denied(text: str, terms: set[str], longest: int) -> bool:
    words = _words(text)
    for i in range(len(words)):
        for n in range(1, min(longest, len(words) - i) + 1):
            chunk = words[i : i + n]
            if " ".join(chunk) in terms or (chunk[0].isdigit() and "".join(chunk) in terms):
                return True
    return False


def _author_line(rel: str, line: str) -> bool:
    """The copyright line of the license names the author on purpose; the denylist skips it and nothing else."""
    return rel == "LICENSE" and line.startswith("Copyright (c) ")


def _google_id(value: str) -> bool:
    return any(c.isupper() for c in value) and any(c.islower() for c in value) and any(c.isdigit() for c in value)


def _line_findings(line: str) -> Iterator[str]:
    for kind, pattern in PATTERNS.items():
        for match in pattern.finditer(line):
            allowed = ALLOWED.get(kind)
            if allowed and allowed.search(match.group(0)):
                continue
            if kind == "google_id" and not _google_id(match.group(1)):
                continue
            yield kind


def _files(root: Path) -> Iterator[Path]:
    for path in sorted(root.rglob("*")):
        parts = path.relative_to(root).parts
        if path.is_file() and not set(parts) & SKIPPED_DIRS and not any(p.endswith(".egg-info") for p in parts):
            yield path


def scan(root: Path, terms: set[str]) -> list[tuple[str, int, str]]:
    """Findings in ``root``: (file, line, kind); line 0 is about the file itself."""
    longest = max((t.count(" ") + 1 for t in terms), default=0) + 5  # a phone may come in several groups
    findings: list[tuple[str, int, str]] = []
    for path in _files(root):
        rel = path.relative_to(root).as_posix()
        if path.name == Path(__file__).name:  # holds the patterns themselves
            continue
        if rel.split("/")[0] in FORBIDDEN_DIRS:
            findings.append((rel, 0, "forbidden_folder"))
            continue
        if FORBIDDEN_FILE.search(rel) or (path.name.startswith(".env") and path.name != ".env.example"):
            findings.append((rel, 0, "forbidden_file"))
            continue
        if path.stat().st_size > MAX_SIZE:
            findings.append((rel, 0, "big_file"))
            continue
        if terms and _denied(rel, terms, longest):
            findings.append((rel, 0, "denylist"))
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except UnicodeDecodeError:
            findings.append((rel, 0, "binary_file"))
            continue
        for number, line in enumerate(lines, 1):
            findings += [(rel, number, kind) for kind in _line_findings(line)]
            if terms and not _author_line(rel, line) and _denied(line, terms, longest):
                findings.append((rel, number, "denylist"))
    return findings


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Privacy scan of a folder before publishing it")
    ap.add_argument("--path", type=Path, default=Path("."), help="folder to scan")
    ap.add_argument("--denylist", type=Path, default=None, help="file with private terms, one per line (optional)")
    args = ap.parse_args(argv)
    findings = scan(args.path.resolve(), load_denylist(args.denylist))
    for rel, line, kind in findings:
        print(f"{rel}:{line}: {kind}")
    print(f"{len(findings)} findings {dict(Counter(kind for _, _, kind in findings))}", file=sys.stderr)
    return 1 if findings else 0


if __name__ == "__main__":
    raise SystemExit(main())
