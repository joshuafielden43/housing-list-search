"""
needs_review.py — optional operator notification when review signals fire.

ADR-0004: suspicious zero and reverification due must not fail the run, but
operators need a hook to notice.

- Logs `NEEDS_REVIEW` at WARNING
- Optional `HLS_NEEDS_REVIEW_WEBHOOK` POST (Hermes, n8n, …)
- Optional Vikunja sync: `HLS_VIKUNJA_URL` + `HLS_VIKUNJA_TOKEN` (+ `HLS_VIKUNJA_PROJECT_ID`, default 9)
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

from housing_list_search.db import DEFAULT_STALE_WARN_THRESHOLD
from housing_list_search.url_policy import URLPolicyError, validate_http_url

logger = logging.getLogger(__name__)

_WEBHOOK_ENV = "HLS_NEEDS_REVIEW_WEBHOOK"


def _safe_operator_url(url: str, *, label: str) -> str | None:
    """Validate operator-configured egress URL; return None when policy blocks."""
    try:
        return validate_http_url(url.strip(), resolve_dns=False)
    except URLPolicyError as exc:
        logger.warning("%s URL blocked by policy: %s", label, exc)
        return None


def should_notify_needs_review(
    *,
    suspicious_zero_authorities: list[str],
    reverification_due_authorities: list[str],
    stale_n: int = 0,
    scrape_failed_n: int = 0,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> bool:
    """True when any Needs Review signal is present."""
    if suspicious_zero_authorities or reverification_due_authorities:
        return True
    if scrape_failed_n > 0:
        return True
    if stale_n >= stale_warn_threshold:
        return True
    return False


def notify_needs_review(
    *,
    run_id: str,
    suspicious_zero_authorities: list[str],
    reverification_due_authorities: list[str],
    stale_n: int = 0,
    scrape_failed_n: int = 0,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> None:
    """Log and optionally POST when Needs Review signals are present."""
    if not should_notify_needs_review(
        suspicious_zero_authorities=suspicious_zero_authorities,
        reverification_due_authorities=reverification_due_authorities,
        stale_n=stale_n,
        scrape_failed_n=scrape_failed_n,
        stale_warn_threshold=stale_warn_threshold,
    ):
        return

    payload: dict[str, Any] = {
        "run_id": run_id,
        "suspicious_zero_authorities": suspicious_zero_authorities,
        "reverification_due_authorities": reverification_due_authorities,
        "stale_n": stale_n,
        "scrape_failed_n": scrape_failed_n,
    }

    logger.warning(
        "NEEDS_REVIEW run_id=%s suspicious_zero=%s reverification_due=%s stale_n=%s scrape_failed_n=%s",
        run_id,
        suspicious_zero_authorities,
        reverification_due_authorities,
        stale_n,
        scrape_failed_n,
    )

    webhook_raw = (os.environ.get(_WEBHOOK_ENV) or "").strip()
    webhook = _safe_operator_url(webhook_raw, label="Needs Review webhook") if webhook_raw else None
    if webhook:
        try:
            resp = requests.post(
                webhook,
                data=json.dumps(payload),
                headers={"Content-Type": "application/json"},
                timeout=15,
            )
            resp.raise_for_status()
            logger.info("Posted Needs Review payload to webhook")
        except Exception as exc:
            logger.warning("Needs Review webhook POST failed: %s", exc)

    from housing_list_search.vikunja_reverification import sync_reverification_tasks

    sync_reverification_tasks(
        run_id=run_id,
        suspicious_zero_authorities=suspicious_zero_authorities,
        reverification_due_authorities=reverification_due_authorities,
        stale_n=stale_n,
        scrape_failed_n=scrape_failed_n,
    )
