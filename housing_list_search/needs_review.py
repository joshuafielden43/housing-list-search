"""
needs_review.py — optional operator notification when review signals fire.

ADR-0004: suspicious zero and reverification due must not fail the run, but
operators need a hook to notice. Set HLS_NEEDS_REVIEW_WEBHOOK to a POST URL
(Hermes, n8n, etc.); when unset, only structured logs are emitted.
"""

from __future__ import annotations

import json
import logging
import os
from typing import Any

import requests

logger = logging.getLogger(__name__)

_WEBHOOK_ENV = "HLS_NEEDS_REVIEW_WEBHOOK"


def notify_needs_review(
    *,
    run_id: str,
    suspicious_zero_authorities: list[str],
    reverification_due_authorities: list[str],
    stale_n: int = 0,
    scrape_failed_n: int = 0,
) -> None:
    """Log and optionally POST when Needs Review signals are present."""
    if not suspicious_zero_authorities and not reverification_due_authorities:
        return

    payload: dict[str, Any] = {
        "run_id": run_id,
        "suspicious_zero_authorities": suspicious_zero_authorities,
        "reverification_due_authorities": reverification_due_authorities,
        "stale_n": stale_n,
        "scrape_failed_n": scrape_failed_n,
    }

    logger.warning(
        "NEEDS_REVIEW run_id=%s suspicious_zero=%s reverification_due=%s",
        run_id,
        suspicious_zero_authorities,
        reverification_due_authorities,
    )

    webhook = (os.environ.get(_WEBHOOK_ENV) or "").strip()
    if not webhook:
        return

    try:
        resp = requests.post(
            webhook,
            data=json.dumps(payload),
            headers={"Content-Type": "application/json"},
            timeout=15,
        )
        resp.raise_for_status()
        logger.info("Posted Needs Review payload to %s", webhook)
    except Exception as exc:
        logger.warning("Needs Review webhook POST failed (%s): %s", webhook, exc)
