"""
measure_registry.py — single source for measure classification.

Handlers live in dispatch.py; this module owns inventory vs portal semantics
used by suspicious_zero and coverage routing.
"""

from __future__ import annotations

from housing_list_search.dispatch import (
    INFORMATIONAL_MEASURES,
    MEASURE_ALIASES,
    SKIP_MEASURES,
)

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
    """True when measures imply per-property inventory, not portal-only."""
    if measures & SKIP_MEASURES:
        return False

    adapter_measures = measures - INFORMATIONAL_MEASURES - SKIP_MEASURES
    if not adapter_measures:
        return False
    if adapter_measures <= PORTAL_ONLY_MEASURES:
        return False
    return bool(adapter_measures & INVENTORY_MEASURES)
