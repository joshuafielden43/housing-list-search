"""
measure_registry.py — single source for measure classification.

Handler registration lives in dispatch.py; this module owns measure sets
used by dispatch, suspicious_zero, coverage routing, and doctor drift checks.
"""

from __future__ import annotations

INFORMATIONAL_MEASURES = frozenset(
    {
        "native_requests",
        "js_heavy",
        "table_based",
        "html_cards",
        "playwright_needed",
        "robots_respect",
        "delegated_administrator",
        "notification_based",
        "monitor_housing_element",
    }
)

SKIP_MEASURES = frozenset({"waf_blocked", "no_public_list"})

MEASURE_ALIASES = {"cdn": "civicplus"}

HANDLER_MEASURES = frozenset(
    {
        "john_stewart",
        "gis",
        "housekeys",
        "civicplus",
        "alta",
        "charities_housing",
        "midpen",
        "eden",
        "eah",
        "first_housing",
    }
)

URL_EXTRACTOR_MEASURES = frozenset({"bloom", "pdf"})

KNOWN_MEASURES = frozenset(
    {
        *HANDLER_MEASURES,
        *URL_EXTRACTOR_MEASURES,
        *SKIP_MEASURES,
        *INFORMATIONAL_MEASURES,
    }
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
