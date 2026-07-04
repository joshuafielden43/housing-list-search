"""
suspicious_zero.py — detect zero-property inventory results needing review.

ADR-0002: property-inventory sources returning zero property records are suspicious.
ADR-0004: flag for operator attention; do not fail the run.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from housing_list_search.coverage import classify_record_kind
from housing_list_search.measure_registry import expects_property_inventory, parse_target_measures
from housing_list_search.validated_zero import has_current_validated_zero


def property_inventory_count(listings: list[dict[str, Any]]) -> int:
    """Count listings classified as property inventory."""
    return sum(1 for item in listings if classify_record_kind(item) == "property")


def find_suspicious_zeros(
    targets: list[dict[str, Any]],
    listings_by_authority: dict[str, list[dict[str, Any]]],
    failed_authorities: list[str],
    *,
    today: date | None = None,
) -> list[str]:
    """
    Authorities that succeeded but returned zero property inventory.

    Failed authorities are excluded — their absence is already SCRAPE_FAILED.
    Targets with a current Validated Zero (ADR-0003) are excluded.
    """
    today = today or date.today()
    failed = set(failed_authorities)
    suspicious: list[str] = []

    for target in targets:
        authority = (target.get("authority") or "").strip()
        if not authority or authority in failed:
            continue

        measures = parse_target_measures(target.get("scraping_measures") or "")
        if not expects_property_inventory(measures):
            continue

        if has_current_validated_zero(target, today=today):
            continue

        listings = listings_by_authority.get(authority, [])
        if property_inventory_count(listings) == 0:
            suspicious.append(authority)

    return sorted(suspicious)
