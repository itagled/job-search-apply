"""Configuration: reads the ``.env`` once and exposes each registered variable.

Every environment variable used by the code is registered here with its default and a
description. It is the only gateway to the environment: the rest of the code does not call ``os.getenv``.
The ``.env.example`` is generated from this registry::

    python -m jobradar.config --check         # which variables are set (without values)
    python -m jobradar.config --env-example   # rewrites .env.example
"""

from __future__ import annotations

import argparse
import os
import threading
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DATA_DIR = ROOT / "data"
SECRETS_DIR = ROOT / "secrets"
PROFILE_DIR = ROOT / "profile"


class ConfigError(RuntimeError):
    """A required variable is missing or has an invalid value."""


@dataclass(frozen=True)
class Var:
    name: str
    description: str
    default: str | None = None
    secret: bool = False
    group: str = "general"
    example: str = ""


GROUPS = {
    "db": "Database",
    "llm": "LLM (NanoGPT or OpenRouter, OpenAI-compatible APIs)",
    "apify": "Apify (paid scrapers)",
    "providers": "Other data providers",
    "google": "Google (Gmail, Calendar, Drive)",
    "personal": "Personalization: profile, language and CV files",
}

VARIABLES: dict[str, Var] = {}


def register(*variables: Var) -> None:
    """Add variables to the registry."""
    for var in variables:
        VARIABLES[var.name] = var


register(
    Var(
        "DATABASE_URL",
        "SQLAlchemy URL of the database: a SQLite file to start (relative to the project), or Postgres "
        "(postgresql://user:password@host:5432/dbname).",
        secret=True,
        group="db",
        example="sqlite:///data/jobradar.db",
    ),
    Var(
        "LLM_PROVIDER",
        "nanogpt | openrouter. Empty: the one whose key is set, NanoGPT first. Jev (the title check and the "
        "pre-filter) is only on NanoGPT.",
        group="llm",
    ),
    Var("NANOGPT_API_KEY", "NanoGPT API key: https://nano-gpt.com/api", secret=True, group="llm"),
    Var("NANOGPT_BASE_URL", "NanoGPT API base URL.", "https://nano-gpt.com/api/v1", group="llm"),
    Var(
        "OPENROUTER_API_KEY",
        "OpenRouter API key, instead of NanoGPT: https://openrouter.ai/settings/keys",
        secret=True,
        group="llm",
    ),
    Var("OPENROUTER_BASE_URL", "OpenRouter API base URL.", "https://openrouter.ai/api/v1", group="llm"),
    Var("FIT_MODEL", "Fit scorer model (final note with explanation).", "deepseek/deepseek-v4-pro", group="llm"),
    Var(
        "FIT_TRIAGE",
        "Cheap pre-filter before the LLM: jev | none. Jev needs NanoGPT: on OpenRouter every posting goes to the LLM.",
        "jev",
        group="llm",
    ),
    Var("FIT_TRIAGE_MIN", "Minimum Jev score (0-100) to pass to the LLM.", "25", group="llm"),
    Var(
        "JEV_MODEL",
        "Jev model (NanoGPT /systemone): the title check and the pre-filter.",
        "typesafe/jev-latest",
        group="llm",
    ),
    Var(
        "TITLE_MODEL",
        "Cheap model that checks job titles where there is no Jev (OpenRouter).",
        "deepseek/deepseek-v4-flash-0731",
        group="llm",
    ),
    Var("TITLE_MIN", "Minimum probability (0-1) for a job title to pass the check.", "0.2", group="llm"),
    Var(
        "CLASSIFY_MODEL",
        "Model that classifies companies (startup / fit with the profile).",
        "deepseek/deepseek-v4-flash-0731",
        group="llm",
    ),
    Var(
        "CLASSIFY_POST_MODEL",
        "Model that decides if a LinkedIn post is a job offer.",
        "deepseek/deepseek-v4-flash-0731",
        group="llm",
    ),
    Var(
        "POSTS_MODEL", "Cheap model that classifies LinkedIn hiring posts.", "google/gemini-3.1-flash-lite", group="llm"
    ),
    Var("LLM_CACHE", "Disk cache of LLM responses (1/0): the same call is not paid twice.", "1", group="llm"),
    Var(
        "APIFY_API_TOKEN",
        "Apify API token: https://console.apify.com/settings/integrations",
        secret=True,
        group="apify",
    ),
    Var("APIFY_LINKEDIN_JOBS_ACTOR", "LinkedIn Jobs listings actor.", "valig/linkedin-jobs-scraper", group="apify"),
    Var(
        "APIFY_LINKEDIN_PROFILE_POSTS_ACTOR",
        "Actor for posts of a LinkedIn profile.",
        "harvestapi/linkedin-profile-posts",
        group="apify",
    ),
    Var(
        "APIFY_EMPLOYEES_ACTOR",
        "Actor for employees of a company on LinkedIn.",
        "harvestapi/linkedin-company-employees",
        group="apify",
    ),
    Var(
        "APIFY_EMPLOYEES_LOCATIONS",
        "Countries where to search employees, comma-separated (default: the country in the profile).",
        group="apify",
    ),
    Var("APIFY_SERP_ACTOR", "Google results actor.", "scraperlink/google-search-results-serp-scraper", group="apify"),
    Var("APOLLO_API_KEY", "Apollo API key (companies and decision-maker emails).", secret=True, group="providers"),
    Var("SERPER_API_KEY", "Serper API key (Google Search), enrichment fallback.", secret=True, group="providers"),
    Var("GMAIL_CLIENT_ID", "OAuth client id to read Gmail alerts (also works in CI).", secret=True, group="google"),
    Var("GMAIL_CLIENT_SECRET", "OAuth client secret to read Gmail alerts.", secret=True, group="google"),
    Var("GMAIL_REFRESH_TOKEN", "Refresh token (scope gmail.readonly) to read alerts.", secret=True, group="google"),
    Var("GMAIL_TOKEN_URI", "Google token endpoint.", "https://oauth2.googleapis.com/token", group="google"),
    Var(
        "GMAIL_SENDER", "Email from which applications and drafts are sent.", group="google", example="you@example.com"
    ),
    Var("GMAIL_SENDER_NAME", "Name shown as sender.", group="google", example="First Last"),
    Var(
        "GMAIL_READ_CLIENT_SECRETS",
        "OAuth credentials (JSON) to regenerate the read token.",
        group="google",
        example="secrets/gmail_read_credentials.json",
    ),
    Var(
        "GMAIL_READ_TOKEN_PATH",
        "Where to save the read token when reauthorizing.",
        group="google",
        example="secrets/token_gmail.json",
    ),
    Var(
        "PROFILE_PATH",
        "Target profile: TOML header with search/filter/scoring settings + ideal-role text.",
        "profile/target.md",
        group="personal",
    ),
    Var("UI_LANGUAGE", "Language of the status page: en | es.", "en", group="personal"),
    Var(
        "CV_FILE_PREFIX",
        "Prefix of the CV files: <prefix>_<Company> (tailored) and <prefix>_<EN|ES> (masters).",
        "CV",
        group="personal",
        example="CV_First_Last",
    ),
)

_loaded = False
_lock = threading.Lock()


def load(env_file: Path | None = None) -> None:
    """Load the ``.env`` once. What is already in the environment is not overwritten."""
    global _loaded
    if _loaded:
        return
    with _lock:
        if not _loaded:
            from dotenv import load_dotenv

            load_dotenv(env_file or ROOT / ".env", override=False)
            _loaded = True


def _var(name: str) -> Var:
    try:
        return VARIABLES[name]
    except KeyError:
        raise KeyError(f"{name} is not registered in jobradar.config") from None


def get(name: str) -> str | None:
    """Value of the variable (or its default); ``None`` if there is none."""
    var = _var(name)
    load()
    value = (os.environ.get(name) or "").strip()
    return value or var.default


def require(name: str) -> str:
    """Value of the variable; ``ConfigError`` with a clear message if missing."""
    value = get(name)
    if not value:
        raise ConfigError(f"Missing {name} in .env: {_var(name).description} (see .env.example)")
    return value


def get_float(name: str) -> float | None:
    value = get(name)
    try:
        return float(value) if value is not None else None
    except ValueError:
        raise ConfigError(f"{name} must be a number, got {value!r}") from None


def get_bool(name: str) -> bool:
    return (get(name) or "").lower() in {"1", "true", "yes", "si", "sí", "on"}


def get_list(name: str, sep: str = ",") -> list[str]:
    return [part.strip() for part in (get(name) or "").split(sep) if part.strip()]


def env_example() -> str:
    """Content of the ``.env.example``: each variable with its description and an example value."""
    lines = ["# Generated with: jobradar config --env-example", ""]
    for group, title in GROUPS.items():
        group_vars = [v for v in VARIABLES.values() if v.group == group]
        if not group_vars:
            continue
        lines.append(f"# --- {title} ---")
        for var in group_vars:
            lines.append(f"# {var.description}" + (f" Default: {var.default}" if var.default else ""))
            lines.append(f"{var.name}={var.example}" if var.default is None else f"# {var.name}={var.default}")
        lines.append("")
    return "\n".join(lines)


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Project configuration (without showing values)")
    ap.add_argument("--check", action="store_true", help="which variables are set, with default or missing")
    ap.add_argument("--env-example", action="store_true", help="rewrite .env.example from the registry")
    args = ap.parse_args(argv)
    if args.env_example:
        (ROOT / ".env.example").write_text(env_example(), encoding="utf-8", newline="\n")
        print(f"OK: {ROOT / '.env.example'}")
    if args.check or not args.env_example:
        load()
        for name in sorted(VARIABLES):
            state = (
                "set" if (os.environ.get(name) or "").strip() else ("default" if VARIABLES[name].default else "MISSING")
            )
            print(f"{name:<36} {state}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
