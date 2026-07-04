"""
coverage.py — classify listings as property inventory vs portal pointers.

HouseKeys and similar adapters emit registration records, not unit lists.
Staff and UEO validators need honest counts without inflating property coverage.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Literal

RecordKind = Literal["property", "portal", "program"]

_PORTAL_SOURCE_PREFIXES = ("housekeys:",)
_PROGRAM_NAME_EXACT = frozenset(
    {
        "housing assistance",
        "rental housing",
        "unknown property",
    }
)


def classify_record_kind(item: dict[str, Any]) -> RecordKind:
    """
    Classify a listing dict for coverage metrics.

    - property: per-property or per-unit inventory
    - portal: registration / notification entry point (not a unit list)
    - program: program-level PDF or page extract, not a named property
    """
    source = (item.get("source") or "").strip().lower()
    name = (item.get("property_name") or "").strip()
    name_lower = name.lower()
    administrator = (item.get("administrator") or "").strip().lower()
    status = (item.get("status") or "").strip().lower()
    address = (item.get("address") or "").strip()

    if source.startswith(_PORTAL_SOURCE_PREFIXES):
        return "portal"
    if administrator == "housekeys" or "via housekeys" in name_lower:
        return "portal"
    if status == "registration required" and "housekeys" in (item.get("notes") or "").lower():
        return "portal"

    if name_lower in _PROGRAM_NAME_EXACT:
        return "program"
    if not address and len(name) < 24 and name.isupper() and " " in name:
        return "program"

    return "property"


@dataclass
class CoverageSummary:
    total: int = 0
    property_count: int = 0
    portal_count: int = 0
    program_count: int = 0
    portal_records: list[dict[str, Any]] = field(default_factory=list)
    program_records: list[dict[str, Any]] = field(default_factory=list)

    @property
    def property_inventory_count(self) -> int:
        """Rows that represent actual property inventory (excludes portal + program noise)."""
        return self.property_count


def summarize_coverage(listings: list[dict[str, Any]]) -> CoverageSummary:
    """Tally record kinds for a deduped run listing set."""
    summary = CoverageSummary()
    summary.total = len(listings)

    for item in listings:
        kind = classify_record_kind(item)
        if kind == "portal":
            summary.portal_count += 1
            summary.portal_records.append(item)
        elif kind == "program":
            summary.program_count += 1
            summary.program_records.append(item)
        else:
            summary.property_count += 1

    return summary
