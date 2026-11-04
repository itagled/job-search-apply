"""Single Apify client: runs actors, downloads the dataset and reads the real cost.

- ``run_sync``: one call to ``run-sync-get-dataset-items``, for short runs.
- ``run``: starts the run, waits and downloads the paginated dataset. If the local deadline runs out,
  it aborts the run so it stops charging and, if requested, keeps the partial data.
- The real cost comes from the run (``usageTotalUsd``), not from multiplying events by rate.
- Calls that start a run (and charge) are not retried, to avoid paying twice;
  reads (status, dataset) are.
- Each dataset is saved raw as soon as it arrives (``jobradar.raw``). If it brings third-party data
  (``third_party=True``) it is minimized first and goes to the folder with deletion deadline.
"""

from __future__ import annotations

import logging
import time
from collections.abc import Iterator
from dataclasses import dataclass, field
from typing import Any

from jobradar import config, http_client, raw

log = logging.getLogger(__name__)

API = "https://api.apify.com/v2"
TERMINAL = frozenset({"SUCCEEDED", "FAILED", "ABORTED", "TIMED-OUT"})
PARTIAL = frozenset({"TIMED-OUT", "ABORTED", "ABORTING", "RUNNING"})
PAGE_SIZE = 1000
# Photos and other people's lists (comments, reactions, followers...) are never saved.
_DROP_KEYS = ("photo", "picture", "avatar", "image")
_DROP_LISTS = ("comment", "reaction", "follower", "like", "repost")


class ApifyError(RuntimeError):
    """The actor failed, ended badly, or returned something unexpected."""


class ApifyAuthError(ApifyError):
    """``APIFY_API_TOKEN`` is missing or Apify rejected it (401/403)."""


@dataclass
class ApifyRun:
    items: list[dict[str, Any]]
    run_id: str | None = None
    status: str = ""
    cost_usd: float | None = None
    charged_events: dict[str, Any] = field(default_factory=dict)


def token() -> str | None:
    return config.get("APIFY_API_TOKEN")


def _token() -> str:
    value = token()
    if not value:
        raise ApifyAuthError("APIFY_API_TOKEN is missing in .env")
    return value


def actor_path(actor_id: str) -> str:
    """``user/actor`` → ``user~actor`` (REST API format)."""
    return actor_id.strip().replace("/", "~")


def _check(resp: Any, what: str) -> Any:
    if resp.status_code in (401, 403):
        raise ApifyAuthError(f"{what}: Apify rejected the token (HTTP {resp.status_code})")
    if not resp.ok:
        raise ApifyError(f"{what}: HTTP {resp.status_code}: {resp.text[:300]}")
    try:
        return resp.json()
    except ValueError as exc:
        raise ApifyError(f"{what}: the response is not JSON") from exc


def minimize(obj: Any) -> Any:
    """Copy without photos or other people's lists (Law 21.719: keep only what is useful)."""
    if isinstance(obj, dict):
        out = {}
        for key, value in obj.items():
            low = key.lower()
            if any(part in low for part in _DROP_KEYS):
                continue
            if isinstance(value, (list, dict)) and any(part in low for part in _DROP_LISTS):
                continue
            out[key] = minimize(value)
        return out
    if isinstance(obj, list):
        return [minimize(value) for value in obj]
    return obj


def _save(kind: str | None, items: list[dict], *, third_party: bool, **meta: Any) -> None:
    if kind:
        data = minimize(items) if third_party else items
        raw.append(kind, [data], third_party=third_party, n_items=len(items), **meta)


def run_sync(
    actor_id: str,
    payload: dict[str, Any],
    *,
    timeout_secs: float = 300,
    raw_kind: str | None = None,
    third_party: bool = False,
) -> list[dict[str, Any]]:
    """Run an actor and receive its items in the same call (for short runs)."""
    resp = http_client.post(
        f"{API}/acts/{actor_path(actor_id)}/run-sync-get-dataset-items",
        params={"token": _token(), "timeout": int(timeout_secs)},
        json=payload,
        timeout=timeout_secs + 60,
        retries=0,
    )
    body = _check(resp, f"actor {actor_id}")
    if not isinstance(body, list):
        raise ApifyError(f"actor {actor_id}: expected a list of items, got {type(body).__name__}")
    items = [item for item in body if isinstance(item, dict)]
    _save(raw_kind, items, third_party=third_party, actor=actor_id, input=payload)
    return items


def _run_info(run_id: str, tok: str) -> dict[str, Any]:
    resp = http_client.get(f"{API}/actor-runs/{run_id}", params={"token": tok}, timeout=30)
    return (_check(resp, f"status of run {run_id}") or {}).get("data") or {}


def _dataset_items(dataset_id: str, tok: str) -> Iterator[dict[str, Any]]:
    offset = 0
    while True:
        resp = http_client.get(
            f"{API}/datasets/{dataset_id}/items",
            timeout=120,
            params={"token": tok, "format": "json", "offset": offset, "limit": PAGE_SIZE},
        )
        page = _check(resp, f"dataset {dataset_id}")
        if not isinstance(page, list):
            raise ApifyError(f"dataset {dataset_id}: not a list")
        yield from (item for item in page if isinstance(item, dict))
        if len(page) < PAGE_SIZE:
            return
        offset += PAGE_SIZE


def run(
    actor_id: str,
    payload: dict[str, Any],
    *,
    timeout_secs: float = 900,
    poll_secs: float = 5,
    max_total_charge_usd: float | None = None,
    run_timeout_secs: int | None = None,
    accept_partial: bool = False,
    raw_kind: str | None = None,
    third_party: bool = False,
) -> ApifyRun:
    """Start an actor, wait for it to finish, and download its dataset.

    ``max_total_charge_usd`` sets a spending cap (pay-per-event actors); ``run_timeout_secs``
    is the deadline within Apify. With ``accept_partial``, what has already been scraped (and charged)
    is kept if the run ends in TIMED-OUT/ABORTED or is aborted due to the local deadline.
    """
    tok = _token()
    params: dict[str, Any] = {"token": tok}
    if max_total_charge_usd is not None:
        params["maxTotalChargeUsd"] = max_total_charge_usd
    if run_timeout_secs is not None:
        params["timeout"] = int(run_timeout_secs)
    start = http_client.post(
        f"{API}/acts/{actor_path(actor_id)}/runs", params=params, json=payload, timeout=60, retries=0
    )
    info = (_check(start, f"start {actor_id}") or {}).get("data") or {}
    run_id = info.get("id")
    if not run_id:
        raise ApifyError(f"start {actor_id}: Apify did not return the run id")

    deadline = time.monotonic() + max(1.0, timeout_secs)
    while str(info.get("status")) not in TERMINAL:
        if time.monotonic() >= deadline:
            log.warning("run %s of %s still running when the deadline expires: aborting", run_id, actor_id)
            resp = http_client.post(f"{API}/actor-runs/{run_id}/abort", params={"token": tok}, timeout=30)
            info = (_check(resp, f"abort run {run_id}") or {}).get("data") or info
            break
        time.sleep(max(0.5, poll_secs))
        info = _run_info(run_id, tok)

    status = str(info.get("status") or "")
    if status != "SUCCEEDED" and not (accept_partial and status in PARTIAL):
        raise ApifyError(f"run {run_id} of {actor_id} ended with {status or 'unknown status'}")
    dataset_id = info.get("defaultDatasetId")
    items = list(_dataset_items(dataset_id, tok)) if dataset_id else []
    info = _run_info(run_id, tok) or info  # the final cost is ready when the run closes
    cost = info.get("usageTotalUsd")
    result = ApifyRun(
        items, run_id, status, float(cost) if cost is not None else None, info.get("chargedEventCounts") or {}
    )
    _save(
        raw_kind,
        items,
        third_party=third_party,
        actor=actor_id,
        run_id=run_id,
        status=status,
        cost_usd=result.cost_usd,
        input=payload,
    )
    log.info("apify %s run=%s %s items=%d cost_usd=%s", actor_id, run_id, status, len(items), result.cost_usd)
    return result
