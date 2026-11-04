"""Single ``jobradar`` command: each subcommand is the ``main()`` of a module, with its own options.

jobradar --help
jobradar sources run --free --days 7
jobradar fit --limit 50
jobradar web --port 8765
"""

from __future__ import annotations

import importlib
import logging
import sys

# subcommand → (module with main(argv), description).
COMMANDS: dict[str, tuple[str, str]] = {
    "sources": ("jobradar.sources.__main__", "download, filter, deduplicate and load postings from each job board"),
    "profile": ("jobradar.profile", "check your target profile (profile/target.md)"),
    "fit": ("jobradar.fit.run", "score postings against the target profile (Jev + LLM)"),
    "companies": ("jobradar.companies.run", "target companies funnel (extract, enrich, classify, contacts)"),
    "mark-application": ("jobradar.outreach.mark_application", "record the outcome of an application"),
    "events": ("jobradar.outreach.app_events", "lifecycle events of each application"),
    "scan-email": ("jobradar.outreach.gmail_scan", "find replies and meetings in Gmail and Calendar"),
    "decision-makers": ("jobradar.outreach.select_candidates", "pick the postings to look for decision makers"),
    "save-contact": ("jobradar.outreach.save_contact", "save a decision maker and their email"),
    "send-outreach": ("jobradar.outreach.send_outreach", "send the ready emails or leave them as drafts"),
    "send-drafts": ("jobradar.outreach.send_drafts", "send the scheduled drafts"),
    "refresh-drafts": ("jobradar.outreach.refresh_drafts", "rebuild the drafts with the new CV"),
    "scheduled-send": ("jobradar.outreach.sender_loop", "send emails during business hours, spaced out"),
    "watch-pending": ("jobradar.outreach.watch_pending", "notify half-done applications"),
    "apollo": ("jobradar.apollo", "find decision makers and reveal their work email"),
    "cv": ("jobradar.cv.gdrive_cv", "tailored CV per company (Google Drive)"),
    "web": ("jobradar.web.status", "local page with the status of the applications"),
    "google": ("jobradar.gapi.auth", "Google credentials: reauthorize and see scopes"),
    "config": ("jobradar.config", "configuration variables (values hidden)"),
    "raw": ("jobradar.raw", "delete expired raw files with third-party data"),
}


def setup_logging() -> None:
    """Progress and warnings go to stderr, so stdout carries only each command's result."""
    logging.basicConfig(
        level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s: %(message)s", datefmt="%H:%M:%S"
    )


def main(argv: list[str] | None = None) -> int:
    for stream in (sys.stdout, sys.stderr):
        if hasattr(stream, "reconfigure"):
            stream.reconfigure(encoding="utf-8", errors="replace")
    setup_logging()
    argv = list(sys.argv[1:] if argv is None else argv)
    if not argv or argv[0] in ("-h", "--help"):
        print("usage: jobradar <command> [options]   (jobradar <command> --help for its options)\n")
        for name, (_, description) in COMMANDS.items():
            print(f"  {name:<22} {description}")
        return 0
    name, rest = argv[0], argv[1:]
    if name not in COMMANDS:
        print(f"unknown command: {name} (see: jobradar --help)", file=sys.stderr)
        return 2
    module = importlib.import_module(COMMANDS[name][0])
    sys.argv = [f"jobradar {name}", *rest]
    return int(module.main(rest) or 0)


if __name__ == "__main__":
    raise SystemExit(main())
