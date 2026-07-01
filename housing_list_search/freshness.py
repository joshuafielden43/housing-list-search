"""
freshness.py — unified change semantics for diff.csv and changelog.

Listing identity: (authority, property_name, url) — same key the DB uses.

Vocabulary:
  diff.csv (machine):     NEW | UPDATED | STALE | SCRAPE_FAILED
  changelog (staff):      ADDED | REMOVED | STATUS_CHANGE | STALE | NO_CHANGE | ...

Mapping:
  ADDED         — in current run set, absent from run_prev snapshot
  REMOVED       — in run_prev snapshot, absent from current run set
  STATUS_CHANGE — same identity in both, display status differs
  STALE         — in DB diff.csv as STALE (not confirmed this run); staff alert
"""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from housing_list_search.status_labels import resolve_status_label

ListingKey = tuple[str, str, str]


def listing_identity(item: dict[str, Any]) -> ListingKey:
    """Canonical (authority, property_name, url) for a listing dict or snapshot row."""
    auth = (item.get("authority") or item.get("source_authority") or "").strip()
    name = (item.get("property_name") or "").strip()
    url = (item.get("url") or item.get("document_url") or "").strip()
    return auth, name, url


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


def stale_from_db_rows(
    diff_rows: list[dict[str, str]],
    *,
    removed_keys: set[ListingKey],
) -> list[ListingKey]:
    """
    STALE rows from diff.csv that are not already covered by REMOVED changelog events.
    """
    stale: list[ListingKey] = []
    for row in diff_rows:
        if row.get("change_type") != "STALE":
            continue
        key = (
            (row.get("source_authority") or row.get("authority") or "").strip(),
            (row.get("property_name") or "").strip(),
            (row.get("url") or "").strip(),
        )
        if key not in removed_keys:
            stale.append(key)
    return stale


def load_diff_csv_rows(path: str = "diff.csv") -> list[dict[str, str]]:
    import csv

    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []