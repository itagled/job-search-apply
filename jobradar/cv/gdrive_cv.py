#!/usr/bin/env python3
"""CVs in Google Drive via API (no Docs API): Word ↔ Google Docs ↔ PDF.

The Docs API is disabled in the Google Cloud project, so everything goes
through the Drive API: the Google Doc is exported as .docx, edited with python-docx
(preserving formatting), uploaded converted to Google Docs, and exported to PDF.

Masters: "<CV_FILE_PREFIX>_EN" and "<CV_FILE_PREFIX>_ES". Their IDs and those of the folders
are in ``profile/cv_masters.json``, which is not uploaded (copy ``profile/cv_masters.example.json``).

CLI (for agents that customize CVs):
    jobradar cv text --lang en                 # master text (to choose what to replace)
    jobradar cv tailor --company "Plane" --lang en --edits edits.json
        edits.json = [{"find": "exact master text", "replace": "new text"}, ...]
        → Google Doc "<CV_FILE_PREFIX>_<Company>" in the personalized CVs folder
          + PDF in profile/cv/companies/<CV_FILE_PREFIX>_<Company>.pdf
"""

from __future__ import annotations

import argparse
import io
import json
import re
import unicodedata
from collections.abc import Iterable
from pathlib import Path
from typing import Any

from jobradar import config
from jobradar.gapi import drive  # noqa: E402

STATE = config.PROFILE_DIR / "cv_masters.json"  # master and folder IDs (outside git)
OUT_DIR = config.ROOT / "profile" / "cv" / "companies"
TAILORED_FOLDER = "Tailored CVs"


# ---------- .docx editing preserving formatting ----------


def _iter_paragraphs(doc: Any) -> Iterable:
    """All paragraphs in the XML: body, cells, and headers/footers (the title under the name lives in the header)."""
    roots = [doc.element.body]
    seen = set()
    for section in doc.sections:
        for part in (
            section.header,
            section.first_page_header,
            section.even_page_header,
            section.footer,
            section.first_page_footer,
            section.even_page_footer,
        ):
            try:
                el = part._element
            except Exception:  # noqa: BLE001 - nonexistent header
                continue
            if el is not None and el not in seen:
                seen.add(el)
                roots.append(el)
    return [p for root in roots for p in root.iter() if p.tag.endswith("}p")]


def _replace_in_paragraph(p_el: Any, find: str, repl: str) -> int:
    from docx.text.paragraph import Paragraph

    para = Paragraph(p_el, None)
    full = para.text
    if find not in full:
        return 0
    runs = para.runs
    for r in runs:  # simple case: all in one run
        if find in r.text:
            r.text = r.text.replace(find, repl)
            return 1
    # crosses runs: text is rewritten preserving the format of each run where possible
    new_full = full.replace(find, repl)
    start = full.find(find)
    pos, first_idx = 0, None
    for i, r in enumerate(runs):
        if pos <= start < pos + len(r.text) and first_idx is None:
            first_idx = i
        pos += len(r.text)
    first_idx = first_idx or 0
    prefix = "".join(r.text for r in runs[:first_idx])
    rest = new_full[len(prefix) :]
    # preserve runs after the end of the match
    end = start + len(find)
    pos, tail_idx = 0, len(runs)
    for i, r in enumerate(runs):
        if pos >= end and i > first_idx:
            tail_idx = i
            break
        pos += len(r.text)
    tail = "".join(r.text for r in runs[tail_idx:])
    runs[first_idx].text = rest[: len(rest) - len(tail)] if tail else rest
    for r in runs[first_idx + 1 : tail_idx]:
        r.text = ""
    return 1


def apply_edits(data: bytes, edits: list[dict]) -> tuple[bytes, list[str]]:
    """Applies exact replacements (throughout the document). Returns (docx, list of 'find' not found)."""
    import docx

    doc = docx.Document(io.BytesIO(data))
    missing = []
    for e in edits:
        n = sum(_replace_in_paragraph(p, e["find"], e["replace"]) for p in _iter_paragraphs(doc))
        if n == 0:
            missing.append(e["find"])
    out = io.BytesIO()
    doc.save(out)
    return out.getvalue(), missing


# ---------- per-company customization ----------


def _state() -> dict:
    if not STATE.is_file():
        raise FileNotFoundError("Missing profile/cv_masters.json (copy profile/cv_masters.example.json)")
    return json.loads(STATE.read_text(encoding="utf-8"))


def safe_company(name: str) -> str:
    s = unicodedata.normalize("NFKD", name)
    s = "".join(c for c in s if not unicodedata.combining(c))
    s = re.sub(r"[^A-Za-z0-9]+", "_", s).strip("_")
    return s[:40] or "Company"


def tailor(company: str, lang: str, edits: list[dict]) -> dict:
    st = _state()
    master = st[f"master_{lang}"]
    svc = drive.service()
    folder = st.get("tailored_folder") or drive.ensure_folder(TAILORED_FOLDER, st.get("parent"), svc=svc)
    data, missing = apply_edits(drive.export_docx(master, svc=svc), edits)
    name = f"{config.get('CV_FILE_PREFIX')}_{safe_company(company)}"
    doc_id = drive.upload_as_gdoc(data, name, folder, svc=svc)
    pdf = drive.export_pdf(doc_id, OUT_DIR / f"{name}.pdf", svc=svc)
    return {
        "doc_id": doc_id,
        "doc_url": f"https://docs.google.com/document/d/{doc_id}/edit",
        "pdf": str(pdf),
        "missing_finds": missing,
        "name": name,
    }


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="CVs in Drive (master text / customize per company)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    t = sub.add_parser("text")
    t.add_argument("--lang", choices=["en", "es"], required=True)
    tl = sub.add_parser("tailor")
    tl.add_argument("--company", required=True)
    tl.add_argument("--lang", choices=["en", "es"], required=True)
    tl.add_argument("--edits", required=True, help="JSON with [{find, replace}] (file)")
    args = ap.parse_args(argv)
    if args.cmd == "text":
        print(drive.export_text(_state()[f"master_{args.lang}"]))
    else:
        edits = json.loads(Path(args.edits).read_text(encoding="utf-8"))
        print(json.dumps(tailor(args.company, args.lang, edits), ensure_ascii=False, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
