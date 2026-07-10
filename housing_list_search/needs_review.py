"""
needs_review.py — optional operator notification when review signals fire.

ADR-0004: suspicious zero and reverification due must not fail the run, but
operators need a hook to notice.

- Logs `NEEDS_REVIEW` at WARNING
- Optional `HLS_NEEDS_REVIEW_WEBHOOK` POST (Hermes, n8n, …)
- Optional Vikunja sync: `HLS_VIKUNJA_URL` + `HLS_VIKUNJA_TOKEN` (+ `HLS_VIKUNJA_PROJECT_ID`, default 9)
"""

from __future__ import annotations

import logging
import os
from typing import Any

from housing_list_search.access import URLPolicyError, polite_post, validate_http_url
from housing_list_search.db import DEFAULT_STALE_WARN_THRESHOLD

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


def surface_run_review(
    *,
    run_id: str,
    suspicious_zero_authorities: list[str] | None = None,
    reverification_due_authorities: list[str] | None = None,
    stale_n: int = 0,
    scrape_failed_n: int = 0,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> None:
    """Primary Needs Review seam (#783): detect signals → log → webhook → Vikunja.

    Detection stays in suspicious_zero / validated_zero; this module owns transport
    only. Safe no-op when no signals fire (ADR-0004: does not fail the run).
    """
    suspicious_zero_authorities = list(suspicious_zero_authorities or [])
    reverification_due_authorities = list(reverification_due_authorities or [])
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

    _post_webhook(payload)
    _sync_vikunja(
        run_id=run_id,
        suspicious_zero_authorities=suspicious_zero_authorities,
        reverification_due_authorities=reverification_due_authorities,
        stale_n=stale_n,
        scrape_failed_n=scrape_failed_n,
    )


def notify_needs_review(
    *,
    run_id: str,
    suspicious_zero_authorities: list[str],
    reverification_due_authorities: list[str],
    stale_n: int = 0,
    scrape_failed_n: int = 0,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> None:
    """Backward-compatible alias for surface_run_review (#783)."""
    surface_run_review(
        run_id=run_id,
        suspicious_zero_authorities=suspicious_zero_authorities,
        reverification_due_authorities=reverification_due_authorities,
        stale_n=stale_n,
        scrape_failed_n=scrape_failed_n,
        stale_warn_threshold=stale_warn_threshold,
    )


def _post_webhook(payload: dict[str, Any]) -> None:
    webhook_raw = (os.environ.get(_WEBHOOK_ENV) or "").strip()
    webhook = _safe_operator_url(webhook_raw, label="Needs Review webhook") if webhook_raw else None
    if not webhook:
        return
    try:
        resp = polite_post(
            webhook,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp is not None:
            resp.raise_for_status()
            logger.info("Posted Needs Review payload to webhook")
        else:
            logger.warning("Needs Review webhook POST blocked by policy")
    except Exception as exc:
        from urllib.parse import urlparse

        try:
            host = urlparse(webhook_raw).netloc or "webhook"
        except Exception:
            host = "webhook"
        logger.warning("Needs Review webhook POST failed for host=%s: %s", host, exc)


def _sync_vikunja(
    *,
    run_id: str,
    suspicious_zero_authorities: list[str],
    reverification_due_authorities: list[str],
    stale_n: int,
    scrape_failed_n: int,
) -> None:
    from housing_list_search.vikunja_reverification import sync_reverification_tasks

    sync_reverification_tasks(
        run_id=run_id,
        suspicious_zero_authorities=suspicious_zero_authorities,
        reverification_due_authorities=reverification_due_authorities,
        stale_n=stale_n,
        scrape_failed_n=scrape_failed_n,
    )
