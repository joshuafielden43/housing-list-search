"""
freshness.py — unified change semantics for diff.csv and changelog.

Listing identity: (authority, property_name, url) — same key the DB uses.

Vocabulary:
  diff.csv (machine):     NEW | UPDATED | STALE | SCRAPE_FAILED
  changelog (staff):      ADDED | REMOVED | STATUS_CHANGE | STALE | SCRAPE_FAILED | NO_CHANGE | ...

Mapping:
  ADDED         — in current run set, absent from run_prev snapshot
  REMOVED       — in run_prev snapshot, absent from current run set after successful authority scrape
  STATUS_CHANGE — same identity in both, display status differs
  STALE         — in DB diff.csv as STALE (not confirmed this run); staff alert
  SCRAPE_FAILED — authority scrape failed; projects diff.csv / failed_authorities (not a closure)
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from housing_list_search.listing import ListingKey, listing_identity
from housing_list_search.status_labels import resolve_status_label


def listings_by_key(items: list[dict[str, Any]]) -> dict[ListingKey, dict[str, Any]]:
    keyed: dict[ListingKey, dict[str, Any]] = {}
    for item in items:
        keyed[listing_identity(item)] = item
    return keyed


@dataclass
class RunDiff:
    added: list[ListingKey] = field(default_factory=list)
    removed: list[ListingKey] = field(default_factory=list)
    status_changed: list[tuple[ListingKey, str, str]] = field(default_factory=list)


def compute_run_diff(
    prev_items: list[dict[str, Any]],
    current_items: list[dict[str, Any]],
) -> RunDiff:
    """Diff run_prev snapshot against this run's deduped listing set."""
    prev = listings_by_key(prev_items)
    curr = listings_by_key(current_items)

    added = [k for k in curr if k not in prev]
    removed = [k for k in prev if k not in curr]

    changed: list[tuple[ListingKey, str, str]] = []
    for key in curr:
        if key not in prev:
            continue
        old_status = resolve_status_label(prev[key])
        new_status = resolve_status_label(curr[key])
        if old_status and new_status and old_status != new_status:
            changed.append((key, old_status, new_status))

    return RunDiff(added=added, removed=removed, status_changed=changed)


def key_from_diff_row(row: dict[str, str]) -> ListingKey:
    """Build ListingKey from a diff.csv row (handles source_authority vs authority column)."""
    return listing_identity(
        {
            "authority": row.get("source_authority") or row.get("authority") or "",
            "property_name": row.get("property_name") or "",
            "url": row.get("url") or "",
        }
    )


def load_diff_csv_rows(path: str = "diff.csv") -> list[dict[str, str]]:
    import csv

    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []
