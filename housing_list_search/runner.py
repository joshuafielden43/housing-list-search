"""
runner.py — measure-driven target dispatcher

run_target(target_row) delegates to dispatch.dispatch_target().
See dispatch.py for the unified measure + URL extractor registry.
"""

from __future__ import annotations

import logging
from typing import Any

from housing_list_search.dispatch import TargetContext, dispatch_target, extract_target
from housing_list_search.measure_registry import parse_target_measures

logger = logging.getLogger(__name__)

__all__ = ["run_target", "extract_target"]


def run_target(target: dict[str, Any], *, failures: list[str] | None = None) -> list[dict]:
    """
    Dispatch one TARGETS.md row to the appropriate adapter(s).

    target: a dict with keys authority, url, scraping_measures,
            administrator, administrator_url, administrator_phone,
            administrator_contact, notes.

    Returns a list of plain dicts ready for dedupe + upsert.
    """
    measures = parse_target_measures(target.get("scraping_measures") or "")

    ctx = TargetContext(
        authority=target.get("authority", ""),
        url=target.get("url", ""),
        measures=measures,
        administrator=target.get("administrator") or "",
        administrator_url=target.get("administrator_url") or "",
        administrator_phone=target.get("administrator_phone") or "",
        administrator_contact=target.get("administrator_contact") or "",
        notes=target.get("notes") or "",
    )

    return dispatch_target(ctx, failures=failures)
