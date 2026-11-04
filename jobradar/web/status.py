#!/usr/bin/env python3
"""Local web page with the status of applications, read live from the database.

    jobradar web            # http://127.0.0.1:8765
    jobradar web --port 9000

Shows upcoming interviews, pending tasks (tests, cases, responses), incomplete applications, and the table of
all applications (stages from ``jobradar/db/applications.py``). The buttons "Done", "I applied", and "Discard" write to the
database (an event in ``application_event`` or the job posting status via ``jobradar/outreach/mark_application.py``).
Texts follow ``UI_LANGUAGE`` (``web/texts.py``). Only listens on 127.0.0.1. No dependencies outside the project's.
"""

from __future__ import annotations

import argparse
import html
import re
from datetime import UTC, datetime, timedelta
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from importlib.resources import files
from string import Template
from typing import Any
from urllib.parse import parse_qs

from sqlalchemy.orm import Session

from jobradar import config
from jobradar.db import applications
from jobradar.db.session import SessionLocal
from jobradar.outreach.mark_application import record_outcome
from jobradar.web.texts import TEXTS

E = html.escape
GM = "https://mail.google.com/mail/u/0/#all/"
# order of the stages (jobradar/db/applications.py) in the table and the filter chips
STAGES = [
    "offer",
    "interview",
    "interview_requested",
    "assessment",
    "replied",
    "incomplete",
    "no_reply",
    "rejected",
]
PAGE = Template(files("jobradar.web").joinpath("status.html").read_text(encoding="utf-8"))


def texts() -> dict[str, Any]:
    return TEXTS.get(config.get("UI_LANGUAGE") or "en", TEXTS["en"])


# ---------------------------------------------------------------- format
def date(x: datetime | None, hour: bool = True) -> str:
    if not x:
        return ""
    t = texts()
    x = x.astimezone()  # local time
    s = f"{t['weekdays'][x.weekday()]} {x.day}-{t['months'][x.month - 1]}"
    return s + (x.strftime(" · %H:%M") if hour else "")


def btn(url: str | None, label: str, kind: str = "") -> str:
    return f'<a class="btn {kind}" href="{E(url)}" target="_blank" rel="noopener">{E(label)} ↗</a>' if url else ""


def form_btn(action: str, ident: int, label: str, kind: str = "", confirm: str = "") -> str:
    on_submit = f" onsubmit=\"return confirm('{E(confirm)}')\"" if confirm else ""
    return (
        f'<form method="post" action="/action" class="inl"{on_submit}><input type="hidden" name="a" value="{action}">'
        f'<input type="hidden" name="id" value="{ident}"><button class="{kind}">{E(label)}</button></form>'
    )


def clean(text: str | None) -> str:
    text = re.sub(r"https?://\S+", "", text or "")
    text = re.sub(
        r"(?:el |la )?(?:texto|nota|DM completo)?\s*guardad[oa]s? en application_submitted(?:\.answers)?",
        texts()["ready_text"],
        text,
    )
    return re.sub(r"\s*\(resume_id \d+\)", "", text).strip()


def card(title: str, when: str, body: str, links: list[str], tag: str = "") -> str:
    return (
        f'<section class="card {tag}"><div class="hd"><h3>{E(title)}</h3><span class="when">{E(when)}</span></div>'
        f'<p>{body}</p><div class="links">{"".join(links)}</div></section>'
    )


# ---------------------------------------------------------------- data
def render() -> str:
    with SessionLocal() as s:
        return _render(s)


def _render(s: Session) -> str:
    t = texts()
    now = datetime.now(UTC)

    # Upcoming interviews
    ups = applications.upcoming_interviews(s)
    ups = sorted(ups, key=lambda r: r["scheduled_for"])
    up_html = (
        "".join(
            card(
                f"{r['company']} · {r['title'] or ''}",
                date(r["scheduled_for"]),
                E(clean(r["summary"])),
                [
                    btn(r["url"], t["join"], "pri"),
                    btn(r["post_url"], t["posting"]),
                    btn(GM + r["gmail_thread_id"] if r["gmail_thread_id"] else "", t["email"]),
                ],
                "hot",
            )
            for r in ups
        )
        or f'<p class="mute">{t["no_interviews"]}</p>'
    )

    # Recent interviews (last 10 days) with their last movement
    rec = applications.recent_interviews(s)
    rec_html = "".join(
        f"<li><b>{E(r['company'])}</b> ({date(r['happened_at'], False)}): {E(clean(r['latest']))}</li>" for r in rec
    )

    # Tasks: requested tests/cases and pending responses, one per job posting
    tasks = applications.pending_tasks(s)
    tasks = sorted(
        tasks, key=lambda r: (r["scheduled_for"] is None, r["scheduled_for"] or now, -r["occurred_at"].timestamp())
    )
    task_html = ""
    for r in tasks:
        extra = []
        try:
            extra = (r["raw"] or {}).get("links") or []
        except AttributeError:
            pass
        when = (
            (t["due"] + date(r["scheduled_for"]))
            if r["scheduled_for"]
            else (t["since"] + date(r["occurred_at"], False))
        )
        label = t["reply"] if r["kind"] == "reply" else t["open"]
        links = [btn(r["url"] or (GM + r["gmail_thread_id"] if r["gmail_thread_id"] else ""), label, "pri")]
        links += [btn(u, lbl) for lbl, u in extra]
        if r["url"] and r["gmail_thread_id"]:
            links.append(btn(GM + r["gmail_thread_id"], t["email"]))
        links += [btn(r["post_url"], t["posting"]), form_btn("done", r["id"], t["done"], "ok")]
        task_html += card(
            f"{r['company']} · {r['title'] or ''}",
            when,
            E(clean(r["summary"] or r["subject"])),
            links,
            "hot" if r["scheduled_for"] and r["scheduled_for"] - now < timedelta(days=3) else "",
        )
    task_html = task_html or f'<p class="mute">{t["no_tasks"]}</p>'

    # Responses from people (last 14 days)
    replies = applications.recent_replies(s)
    rep_html = "".join(
        f"<li><b>{E(r['company'])}</b> ({date(r['occurred_at'], False)}): {E(clean(r['summary'] or r['subject']))} "
        f"{btn(GM + r['gmail_thread_id'] if r['gmail_thread_id'] else '', t['email'])}</li>"
        for r in replies
    )

    # Incomplete applications (missing a step from you to submit them)
    pending = applications.incomplete_applications(s)
    pending = sorted(pending, key=lambda r: r["at"] or now, reverse=True)
    pending_html = "".join(
        f'<li><b>{E(r["company"] or "")}</b> · {E(r["title"] or "")} <span class="tag">{E((r["reason"] or "")[:40])}</span>'
        f'<div class="mute">{E(clean(r["note"])[:260])}</div><div class="links">{btn(r["url"], t["open"])}'
        f"{form_btn('applied', r['id'], t['applied'], 'ok')}"
        f"{form_btn('discard', r['id'], t['discard'], '', t['discard_confirm'])}</div></li>"
        for r in pending
    )

    # Table of all applications
    order = {stage: i for i, stage in enumerate(STAGES)}
    apps = applications.all_applications(s)
    apps = sorted(
        apps, key=lambda a: (order.get(a["stage"], 9), -(a["last_event_at"].timestamp() if a["last_event_at"] else 0))
    )
    counts: dict[str, int] = {}
    for a in apps:
        counts[a["stage"]] = counts.get(a["stage"], 0) + 1
    trs = []
    for a in apps:
        label = t["stages"].get(a["stage"], a["stage"])
        links = btn(a["post_url"], t["posting"]) + (
            btn(GM + a["gmail_thread_id"], t["email"]) if a["gmail_thread_id"] else ""
        )
        trs.append(
            f'<tr data-stage="{E(a["stage"])}"><td><b>{E(a["company"] or "")}</b><div class="mute">{E((a["title"] or "")[:70])}</div></td>'
            f'<td><span class="tag t-{E(a["stage"])}">{E(label)}</span></td><td>{date(a["applied_at"], False)}</td>'
            f'<td><span class="mute">{date(a["last_at"] or a["last_event_at"], False)}</span> {E(clean(a["last_summary"])[:150])}</td>'
            f'<td class="lk">{links}</td></tr>'
        )
    chips = "".join(
        f'<button class="chip" data-f="{k}">{E(t["stages"][k])} <b>{counts[k]}</b></button>'
        for k in STAGES
        if counts.get(k)
    )

    summary = t["summary"].format(
        interviews=len(ups),
        tasks=len(tasks),
        in_interview=counts.get("interview", 0),
        no_reply=counts.get("no_reply", 0),
        rejected=counts.get("rejected", 0),
    )
    return PAGE.substitute(
        {k: v for k, v in t.items() if k.startswith("t_")},
        lang=t["lang"],
        now=date(now),
        summary=E(summary),
        up=up_html,
        rec=(f'<h3 class="sub3">{t["recent_interviews"]}</h3><ul class="lst">{rec_html}</ul>' if rec_html else ""),
        tasks=task_html,
        rep=(f'<ul class="lst">{rep_html}</ul>' if rep_html else f'<p class="mute">{t["no_replies"]}</p>'),
        npend=len(pending),
        pending=(
            f'<ul class="lst">{pending_html}</ul>' if pending_html else f'<p class="mute">{t["nothing_pending"]}</p>'
        ),
        total=len(apps),
        chips=chips,
        trs="".join(trs),
    )


# ---------------------------------------------------------------- actions
def action(a: str, ident: int) -> str:
    t = texts()
    with SessionLocal() as s:
        if a == "done":
            if not applications.mark_task_done(s, ident, note_prefix=t["marked_done"]):
                return t["task_not_found"]
        elif a in ("applied", "discard"):
            res = (
                record_outcome(s, job_id=ident, status="applied", via="web", note=t["marked_applied"])
                if a == "applied"
                else record_outcome(s, job_id=ident, status="skipped", reason=t["marked_discarded"])
            )
            if res is None:
                return t["posting_not_found"]
        else:
            return t["unknown_action"]
        s.commit()
        return "ok"


class Handler(BaseHTTPRequestHandler):
    def _send(self, code: int, body: str, ctype: str = "text/html; charset=utf-8") -> None:
        data = body.encode("utf-8")
        self.send_response(code)
        self.send_header("Content-Type", ctype)
        self.send_header("Content-Length", str(len(data)))
        self.send_header("Cache-Control", "no-store")
        self.end_headers()
        self.wfile.write(data)

    def do_GET(self) -> None:  # noqa: N802
        if self.path.split("?")[0] not in ("/", "/index.html"):
            return self._send(404, texts()["not_found"], "text/plain; charset=utf-8")
        try:
            self._send(200, render())
        except Exception as e:  # noqa: BLE001
            self._send(500, f"<pre>{texts()['db_error']}: {E(str(e))}</pre>")

    def do_POST(self) -> None:  # noqa: N802
        if self.path != "/action":
            return self._send(404, texts()["not_found"], "text/plain; charset=utf-8")
        n = int(self.headers.get("Content-Length") or 0)
        f = parse_qs(self.rfile.read(n).decode("utf-8"))
        try:
            action(f.get("a", [""])[0], int(f.get("id", ["0"])[0]))
        except Exception as e:  # noqa: BLE001
            return self._send(500, f"<pre>{texts()['save_error']}: {E(str(e))}</pre>")
        self.send_response(303)
        self.send_header("Location", "/")
        self.end_headers()

    def log_message(self, fmt: str, *args: Any) -> None:  # silence
        return


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Local page with the status of applications")
    ap.add_argument("--port", type=int, default=8765)
    args = ap.parse_args(argv)
    srv = ThreadingHTTPServer(("127.0.0.1", args.port), Handler)
    print(f"Application status at http://127.0.0.1:{args.port}  (Ctrl+C to stop)", flush=True)
    try:
        srv.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
