"""Google credentials per profile, a single ``service()`` and a single CLI to reauthorize.

Each profile is a token with its permissions:

- ``google``: Drive, Sheets and Calendar (``secrets/google_token.json``).
- ``gmail_send``: drafts, sends and reading replies (``secrets/gmail_send_token.json``).
- ``gmail_read``: read job alerts. Comes from ``GMAIL_CLIENT_ID``,
  ``GMAIL_CLIENT_SECRET`` and ``GMAIL_REFRESH_TOKEN``, so it also works in CI.

Reauthorize (opens Google consent in the browser)::

    python -m jobradar.gapi.auth reauth google [--no-open] [--port N]
    python -m jobradar.gapi.auth reauth gmail_send [--login-hint EMAIL]
    python -m jobradar.gapi.auth reauth gmail_read
    python -m jobradar.gapi.auth scopes google
"""

from __future__ import annotations

import argparse
import json
import re
import time
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from jobradar import config

SCOPE = "https://www.googleapis.com/auth/"


@dataclass(frozen=True)
class Profile:
    scopes: tuple[str, ...]
    token_file: str | None = None  # in secrets/; None = credentials from the environment
    client_secrets_var: str | None = None  # variable with the path to the OAuth client JSON


PROFILES: dict[str, Profile] = {
    "google": Profile(
        tuple(SCOPE + s for s in ("spreadsheets", "drive", "calendar")),
        token_file="google_token.json",
    ),
    "gmail_send": Profile((SCOPE + "gmail.compose", SCOPE + "gmail.readonly"), token_file="gmail_send_token.json"),
    "gmail_read": Profile((SCOPE + "gmail.readonly",), client_secrets_var="GMAIL_READ_CLIENT_SECRETS"),
}
DEFAULT_CLIENT_SECRETS = "google_credentials.json"


class MissingCredentialsError(RuntimeError):
    """No valid token for the profile: must reauthorize."""


def _profile(name: str) -> Profile:
    if name not in PROFILES:
        raise KeyError(f"Unknown Google profile: {name} (available: {', '.join(PROFILES)})")
    return PROFILES[name]


def _resolve(path: str) -> Path:
    p = Path(path)
    return p if p.is_absolute() else config.ROOT / p


def credentials(name: str) -> Any:
    """Current credentials for the profile; if the token expired, refresh and save it."""
    from google.auth.exceptions import RefreshError
    from google.auth.transport.requests import Request
    from google.oauth2.credentials import Credentials

    profile = _profile(name)
    if profile.token_file is None:
        client_id, secret = config.get("GMAIL_CLIENT_ID"), config.get("GMAIL_CLIENT_SECRET")
        refresh = config.get("GMAIL_REFRESH_TOKEN")
        if not (client_id and secret and refresh):
            raise MissingCredentialsError(
                "missing GMAIL_CLIENT_ID, GMAIL_CLIENT_SECRET or GMAIL_REFRESH_TOKEN in the .env"
            )
        creds = Credentials(
            None,
            refresh_token=refresh,
            token_uri=config.get("GMAIL_TOKEN_URI"),
            client_id=client_id,
            client_secret=secret,
            scopes=list(profile.scopes),
        )
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise MissingCredentialsError(f"Google rejected the token for {name}: reauthorize ({exc})") from exc
        return creds
    path = config.SECRETS_DIR / profile.token_file
    if not path.is_file():
        raise MissingCredentialsError(f"{path} does not exist: run python -m jobradar.gapi.auth reauth {name}")
    creds = Credentials.from_authorized_user_file(str(path))
    if not creds.valid:
        try:
            creds.refresh(Request())
        except RefreshError as exc:
            raise MissingCredentialsError(f"Google rejected the token for {name}: reauthorize ({exc})") from exc
        path.write_text(creds.to_json(), encoding="utf-8")
    return creds


def scopes(name: str) -> list[str]:
    """Permissions the profile token currently has (to warn if any are missing)."""
    profile = _profile(name)
    if profile.token_file is None:
        return list(profile.scopes)
    path = config.SECRETS_DIR / profile.token_file
    return list(json.loads(path.read_text(encoding="utf-8")).get("scopes") or []) if path.is_file() else []


def service(api: str, version: str, profile: str) -> Any:
    """``googleapiclient`` client for the requested API, with the profile credentials."""
    from googleapiclient.discovery import build

    return build(api, version, credentials=credentials(profile), cache_discovery=False)


def execute(request: Any, *, tries: int = 8, idempotent: bool = True) -> Any:
    """Execute a ``googleapiclient`` request retrying per-minute quotas and 5xx errors.

    What it sends or creates (``idempotent=False``) is only retried if Google reports quota
    exceeded, which means it was not executed: after a 5xx it could duplicate an email.
    """
    from googleapiclient.errors import HttpError

    for attempt in range(tries):
        try:
            return request.execute()
        except HttpError as exc:
            status = exc.resp.status
            rate_limited = status in (403, 429) and "ateLimit" in str(exc)
            transient = rate_limited or (idempotent and status >= 500)
            if not transient or attempt == tries - 1:
                raise
            time.sleep(min(60, 15 * (attempt + 1)))
    return None


def _client_secrets(profile: Profile) -> Path:
    custom = config.get(profile.client_secrets_var) if profile.client_secrets_var else None
    return _resolve(custom) if custom else config.SECRETS_DIR / DEFAULT_CLIENT_SECRETS


def _update_env(values: dict[str, str]) -> None:
    """Replace (or add) ``KEY=value`` lines in the ``.env`` without touching the others."""
    env = config.ROOT / ".env"
    text = env.read_text(encoding="utf-8") if env.is_file() else ""
    for key, value in values.items():
        line = f"{key}={value}"
        if re.search(rf"(?m)^{key}=.*$", text):
            text = re.sub(rf"(?m)^{key}=.*$", lambda _, line=line: line, text)
        else:
            text = text.rstrip("\n") + f"\n{line}\n"
    env.write_text(text, encoding="utf-8")


def reauthorize(name: str, *, open_browser: bool = True, port: int = 0, login_hint: str | None = None) -> list[str]:
    """Request Google consent and save the new token for the profile. Returns the scopes."""
    from google_auth_oauthlib.flow import InstalledAppFlow

    profile = _profile(name)
    client_secrets = _client_secrets(profile)
    if not client_secrets.is_file():
        raise MissingCredentialsError(f"cannot find OAuth client credentials in {client_secrets}")
    flow = InstalledAppFlow.from_client_secrets_file(str(client_secrets), list(profile.scopes))
    creds = flow.run_local_server(
        port=port,
        access_type="offline",
        prompt="consent",
        open_browser=open_browser,
        login_hint=login_hint,
        authorization_prompt_message="AUTH_URL: {url}",
    )
    if not creds or not creds.refresh_token:
        raise MissingCredentialsError("no refresh token obtained (was consent cancelled?)")
    if profile.token_file is None:
        _update_env({"GMAIL_REFRESH_TOKEN": creds.refresh_token})
        token_path = config.get("GMAIL_READ_TOKEN_PATH")
        if token_path:  # copy to file for other projects that read the same Gmail
            _resolve(token_path).write_text(creds.to_json(), encoding="utf-8")
    else:
        (config.SECRETS_DIR / profile.token_file).write_text(creds.to_json(), encoding="utf-8")
    return list(creds.scopes or [])


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="Google credentials per profile (without showing tokens)")
    sub = ap.add_subparsers(dest="cmd", required=True)
    r = sub.add_parser("reauth", help="open Google consent and save the new token")
    r.add_argument("profile", choices=sorted(PROFILES))
    r.add_argument("--no-open", action="store_true", help="only print the link (open it in another browser)")
    r.add_argument("--port", type=int, default=0, help="port of the local callback (default: any free port)")
    r.add_argument("--login-hint", default=None, help="suggested account (default: GMAIL_SENDER)")
    s = sub.add_parser("scopes", help="permissions of the saved token")
    s.add_argument("profile", choices=sorted(PROFILES))
    args = ap.parse_args(argv)

    if args.cmd == "scopes":
        print(json.dumps(scopes(args.profile), indent=1))
        return 0
    hint = args.login_hint or (config.get("GMAIL_SENDER") if args.profile.startswith("gmail") else None)
    granted = reauthorize(args.profile, open_browser=not args.no_open, port=args.port, login_hint=hint)
    print(json.dumps({"ok": True, "profile": args.profile, "scopes": granted}, indent=1))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
