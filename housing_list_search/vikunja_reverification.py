"""
vikunja_reverification.py — create/update Reverification Tasks in Vikunja #9.

ADR-0004: Suspicious Zero and reverification-due authorities get operator tasks
without failing the run. Enabled when HLS_VIKUNJA_URL and HLS_VIKUNJA_TOKEN are set.

Optional: HLS_VIKUNJA_PROJECT_ID (default 9).
"""

from __future__ import annotations

import logging
import os
from typing import Any

from housing_list_search.access import URLPolicyError, polite_get, polite_post, validate_http_url

logger = logging.getLogger(__name__)

_REVERIFY_PREFIX = "[Reverify]"
_ENV_URL = "HLS_VIKUNJA_URL"
_ENV_TOKEN = "HLS_VIKUNJA_TOKEN"
_ENV_PROJECT = "HLS_VIKUNJA_PROJECT_ID"
_DEFAULT_PROJECT_ID = 9


def reverify_task_title(authority: str) -> str:
    return f"{_REVERIFY_PREFIX} {authority.strip()}"


def _vikunja_config() -> tuple[str, str, int] | None:
    base_raw = (os.environ.get(_ENV_URL) or "").strip().rstrip("/")
    token = (os.environ.get(_ENV_TOKEN) or "").strip()
    if not base_raw or not token:
        return None
    try:
        base = validate_http_url(base_raw, resolve_dns=False).rstrip("/")
    except URLPolicyError as exc:
        logger.warning("Vikunja URL blocked by policy: %s", exc)
        return None
    raw_project = (os.environ.get(_ENV_PROJECT) or "").strip()
    try:
        project_id = int(raw_project) if raw_project else _DEFAULT_PROJECT_ID
    except ValueError:
        project_id = _DEFAULT_PROJECT_ID
    return base, token, project_id


def _redacted(token: str) -> str:
    """#790: never log full bearer tokens."""
    if not token:
        return ""
    t = token.strip()
    if len(t) <= 8:
        return "***"
    return f"{t[:4]}...{t[-4:]}"


def _safe_log_url(url: str) -> str:
    """Log host+path only — strip query (may embed tokens)."""
    from urllib.parse import urlparse

    try:
        p = urlparse(url)
        return f"{p.scheme}://{p.netloc}{p.path}"
    except Exception:
        return "(url)"


def _headers(token: str) -> dict[str, str]:
    return {
        "Authorization": f"Bearer {token}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


def _list_open_tasks(base: str, token: str, project_id: int) -> list[dict[str, Any]]:
    url = f"{base}/api/v1/projects/{project_id}/tasks"
    resp = polite_get(url, headers=_headers(token))
    if resp is None:
        raise RuntimeError("Vikunja list blocked by policy")
    resp.raise_for_status()
    data = resp.json()
    if isinstance(data, list):
        tasks = data
    else:
        tasks = data.get("tasks") or data.get("items") or []
    return [t for t in tasks if isinstance(t, dict) and not t.get("done")]


def _find_open_task(tasks: list[dict[str, Any]], title: str) -> dict[str, Any] | None:
    for task in tasks:
        if (task.get("title") or "").strip() == title:
            return task
    return None


def _task_description(
    authority: str,
    *,
    run_id: str,
    signals: set[str],
    stale_n: int,
    scrape_failed_n: int,
) -> str:
    signal_lines = []
    if "suspicious_zero" in signals:
        signal_lines.append(
            "- **Suspicious zero** — inventory adapter returned no property records"
        )
    if "reverification_due" in signals:
        signal_lines.append(
            "- **Reverification due** — Validated Zero review date elapsed in TARGETS.md"
        )
    signals_block = "\n".join(signal_lines)
    return (
        f"Authority: **{authority}**\n\n"
        f"Signals:\n{signals_block}\n\n"
        f"Run: `{run_id}`\n"
        f"Integrity: STALE={stale_n}, SCRAPE_FAILED={scrape_failed_n}\n\n"
        "Actions:\n"
        f'1. `python main.py --run --target "{authority}"` — confirm zero vs regression\n'
        "2. If legitimately empty: update **Validated Zero** + **Review Due** in TARGETS.md\n"
        "3. `python scripts/doctor.py --fix` — re-ingest targets\n"
        "4. Close this task when resolved\n"
    )


def _create_task(
    base: str,
    token: str,
    project_id: int,
    *,
    title: str,
    description: str,
) -> int:
    url = f"{base}/api/v1/projects/{project_id}/tasks"
    body = {"title": title, "description": description, "priority": 4}
    resp = polite_post(url, json=body, headers=_headers(token))
    if resp is not None:
        resp.raise_for_status()
    else:
        raise RuntimeError("Vikunja create blocked by policy")
    data = resp.json()
    task_id = data.get("id")
    if task_id is None:
        raise ValueError(f"Vikunja create returned no id: {data!r}")
    return int(task_id)


def _update_task(
    base: str,
    token: str,
    task_id: int,
    *,
    description: str,
) -> None:
    url = f"{base}/api/v1/tasks/{task_id}"
    resp = polite_post(url, json={"description": description}, headers=_headers(token))
    if resp is not None:
        resp.raise_for_status()
    else:
        raise RuntimeError("Vikunja update blocked by policy")


def sync_reverification_tasks(
    *,
    run_id: str,
    suspicious_zero_authorities: list[str],
    reverification_due_authorities: list[str],
    stale_n: int = 0,
    scrape_failed_n: int = 0,
) -> None:
    """Create or update open [Reverify] tasks for authorities needing review."""
    cfg = _vikunja_config()
    if cfg is None:
        return

    base, token, project_id = cfg
    by_authority: dict[str, set[str]] = {}
    for name in suspicious_zero_authorities:
        if name.strip():
            by_authority.setdefault(name.strip(), set()).add("suspicious_zero")
    for name in reverification_due_authorities:
        if name.strip():
            by_authority.setdefault(name.strip(), set()).add("reverification_due")
    if not by_authority:
        return

    try:
        open_tasks = _list_open_tasks(base, token, project_id)
    except Exception as exc:
        logger.warning("Vikunja list tasks failed (%s): %s", _safe_log_url(base), exc)
        return

    for authority, signals in sorted(by_authority.items()):
        title = reverify_task_title(authority)
        description = _task_description(
            authority,
            run_id=run_id,
            signals=signals,
            stale_n=stale_n,
            scrape_failed_n=scrape_failed_n,
        )
        existing = _find_open_task(open_tasks, title)
        try:
            if existing and existing.get("id"):
                _update_task(base, token, int(existing["id"]), description=description)
                logger.info(
                    "Updated Vikunja reverification task #%s for %s", existing["id"], authority
                )
            else:
                task_id = _create_task(
                    base, token, project_id, title=title, description=description
                )
                logger.info("Created Vikunja reverification task #%s for %s", task_id, authority)
        except Exception as exc:
            logger.warning("Vikunja reverification sync failed for %s: %s", authority, exc)
