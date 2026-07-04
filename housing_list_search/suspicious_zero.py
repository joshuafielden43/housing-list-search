"""
suspicious_zero.py — detect zero-property inventory results needing review.

ADR-0002: property-inventory sources returning zero property records are suspicious.
ADR-0004: flag for operator attention; do not fail the run.
"""

from __future__ import annotations

from datetime import date
from typing import Any

from housing_list_search.coverage import classify_record_kind
from housing_list_search.dispatch import (
    INFORMATIONAL_MEASURES,
    MEASURE_ALIASES,
    SKIP_MEASURES,
)
from housing_list_search.validated_zero import has_current_validated_zero

INVENTORY_MEASURES = frozenset(
    {
        "bloom",
        "john_stewart",
        "gis",
        "civicplus",
        "alta",
        "charities_housing",
        "midpen",
        "eden",
        "eah",
        "first_housing",
        "pdf",
    }
)

PORTAL_ONLY_MEASURES = frozenset({"housekeys"})


def parse_target_measures(raw: str) -> set[str]:
    """Normalize scraping_measures from a TARGETS.md row."""
    parts = {m.strip().lower() for m in (raw or "").split(",") if m.strip()}
    return {MEASURE_ALIASES.get(m, m) for m in parts}


def expects_property_inventory(measures: set[str]) -> bool:
    """
    True when a target's measures imply per-property inventory, not portal-only.

    Portal-only targets (HouseKeys registration) may legitimately return zero
    property rows. Informational measures (native_requests, delegated_administrator,
    …) do not change that classification.
    """
    if measures & SKIP_MEASURES:
        return False

    adapter_measures = measures - INFORMATIONAL_MEASURES - SKIP_MEASURES
    if not adapter_measures:
        return False
    if adapter_measures <= PORTAL_ONLY_MEASURES:
        return False
    return bool(adapter_measures & INVENTORY_MEASURES)


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
