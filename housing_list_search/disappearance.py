"""
disappearance.py — deep module for machine Diff labels and staff projection (ADR-0001).

Owns:
  - Machine change_type: NEW | UPDATED | STALE | SCRAPE_FAILED  (pure classify)
  - Staff projection: ADDED | REMOVED | STALE | SCRAPE_FAILED | STATUS_CHANGE
  - ListingKey helpers used by those rules
  - load_diff_csv_rows (thin I/O for projecting from an existing diff.csv)

Does not own: SQLite persistence, CSV/markdown render, when to rewrite run_prev
(pipeline / publish orchestration).

diff.csv is the source of truth for disappearance (ADR-0001). Down ≠ gone:
SCRAPE_FAILED is collector failure, not inventory removal.
"""

from __future__ import annotations

import csv
from collections.abc import Collection
from dataclasses import dataclass, field
from datetime import datetime, timedelta
from typing import Any, Literal

from housing_list_search.listing import ListingKey, canonical_authority, listing_identity
from housing_list_search.status_labels import resolve_status_label

__all__ = [
    "ListingKey",
    "MACHINE_CHANGE_TYPES",
    "MachineChange",
    "RunDiff",
    "DisappearanceResult",
    "expand_scrape_failed_authorities",
    "classify_machine_change",
    "classify_machine_change_without_run_id",
    "listings_by_key",
    "compute_run_diff",
    "key_from_diff_row",
    "load_diff_csv_rows",
    "project_disappearance",
    "listing_identity",
]

MachineChange = Literal["NEW", "UPDATED", "STALE", "SCRAPE_FAILED"]
MACHINE_CHANGE_TYPES: tuple[MachineChange, ...] = ("NEW", "UPDATED", "SCRAPE_FAILED", "STALE")


# ---------------------------------------------------------------------------
# Machine Diff labels (candidate #3 — pure classification)
# ---------------------------------------------------------------------------


def expand_scrape_failed_authorities(
    authorities: list[str] | None = None,
) -> frozenset[str]:
    """Expand failed-authority labels through canonical_authority (#1049).

    TARGETS portfolio names (e.g. \"MidPen Housing (…)\") must match rows stored
    under the canonical authority (\"MidPen Housing\").
    """
    expanded: set[str] = set()
    for a in authorities or []:
        if not a:
            continue
        expanded.add(a)
        canon = canonical_authority(a) or a
        if canon:
            expanded.add(canon)
    return frozenset(expanded)


def classify_machine_change(
    *,
    run_id: str,
    last_run_id: str | None,
    first_run_id: str | None,
    first_seen: str | None = None,
    last_seen: str | None = None,
    authority: str,
    scrape_failed: Collection[str] | None = None,
) -> MachineChange:
    """Classify one housing_records row for diff.csv (run_id path).

    Rules (frozen — do not \"improve\" without product review):
      NEW           — confirmed this run and first confirmation is this run
                      (first_run_id == run_id, or legacy: first_run_id NULL and
                      first_seen == last_seen)
      UPDATED       — confirmed this run, existed before
      SCRAPE_FAILED — not confirmed; authority in scrape_failed set (expanded)
      STALE         — not confirmed; authority scrape succeeded (or unknown)
    """
    failed = scrape_failed or frozenset()
    last = (last_run_id or "").strip()
    rid = (run_id or "").strip()
    if rid and last == rid:
        first_r = first_run_id
        if first_r is not None:
            first_r = str(first_r).strip() or None
        if first_r == rid or (
            first_r is None and (first_seen or "") == (last_seen or "")
        ):
            return "NEW"
        return "UPDATED"
    auth = (authority or "").strip()
    if auth and auth in failed:
        return "SCRAPE_FAILED"
    return "STALE"


def classify_machine_change_without_run_id(
    *,
    first_seen: str | None,
    last_seen: str | None,
    now: datetime | None = None,
    stale_after_days: int = 7,
) -> MachineChange:
    """Fallback when export_diff is called without a run_id (legacy/diagnostic)."""
    if (first_seen or "") == (last_seen or ""):
        return "NEW"
    ref = now or datetime.now()
    parsed = _parse_iso_loose(last_seen)
    if parsed is not None and parsed < ref - timedelta(days=stale_after_days):
        return "STALE"
    return "UPDATED"


def _parse_iso_loose(value: str | None) -> datetime | None:
    if not value:
        return None
    text = value.strip()
    if not text:
        return None
    try:
        # Support trailing Z
        if text.endswith("Z"):
            text = text[:-1] + "+00:00"
        return datetime.fromisoformat(text)
    except ValueError:
        return None


# ---------------------------------------------------------------------------
# Identity helpers + run_prev STATUS_CHANGE diff
# ---------------------------------------------------------------------------


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
    """Diff run_prev snapshot against this run's deduped listing set (STATUS_CHANGE)."""
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
    """Build ListingKey from a diff.csv row (source_authority vs authority)."""
    return listing_identity(
        {
            "authority": row.get("source_authority") or row.get("authority") or "",
            "property_name": row.get("property_name") or "",
            "url": row.get("url") or "",
        }
    )


def load_diff_csv_rows(path: str = "diff.csv") -> list[dict[str, str]]:
    try:
        with open(path, newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))
    except FileNotFoundError:
        return []


# ---------------------------------------------------------------------------
# Staff projection (ADR-0001)
# ---------------------------------------------------------------------------


@dataclass
class DisappearanceResult:
    """Staff-facing change events assembled from diff.csv + run_prev status diff."""

    is_first_run: bool = False
    added: list[ListingKey] = field(default_factory=list)
    removed: list[ListingKey] = field(default_factory=list)
    stale_lingering: list[ListingKey] = field(default_factory=list)
    scrape_failed: list[ListingKey] = field(default_factory=list)
    status_changed: list[tuple[ListingKey, str, str]] = field(default_factory=list)
    prev_count: int = 0
    current_count: int = 0


def _dedupe_keys(keys: list[ListingKey]) -> list[ListingKey]:
    return list(dict.fromkeys(keys))


def project_disappearance(
    *,
    run_id: str,
    previous_run_id: str | None,
    diff_rows: list[dict[str, str]],
    current_listings: list[dict],
    prev_snapshot: list[dict],
    scrape_failed_authorities: list[str] | None = None,
) -> DisappearanceResult:
    """
    Assemble staff changelog events from machine diff + STATUS_CHANGE snapshot.

    ADDED: diff.csv NEW rows.
    REMOVED: diff.csv STALE where last_run_id == previous_run_id (confirmed last full run).
    STALE (lingering): other diff.csv STALE rows.
    SCRAPE_FAILED: diff.csv SCRAPE_FAILED rows only.
    STATUS_CHANGE: run_prev snapshot vs current listings (identity via listing_identity).
    """
    _ = run_id  # reserved for future run_id-scoped projections
    failed = set(scrape_failed_authorities or [])
    is_first_run = not prev_snapshot

    status_changed: list[tuple[ListingKey, str, str]] = []
    if prev_snapshot and current_listings:
        status_changed = compute_run_diff(prev_snapshot, current_listings).status_changed

    added: list[ListingKey] = []
    removed: list[ListingKey] = []
    stale_lingering: list[ListingKey] = []
    scrape_failed: list[ListingKey] = []

    for row in diff_rows:
        change_type = (row.get("change_type") or "").strip()
        key = key_from_diff_row(row)

        if change_type == "NEW":
            added.append(key)
        elif change_type == "SCRAPE_FAILED":
            scrape_failed.append(key)
        elif change_type == "STALE":
            last_rid = (row.get("last_run_id") or "").strip()
            if previous_run_id and last_rid == previous_run_id and key[0] not in failed:
                removed.append(key)
            else:
                stale_lingering.append(key)

    removed_set = set(removed)
    scrape_failed_set = set(scrape_failed)
    stale_lingering = [
        k for k in stale_lingering if k not in removed_set and k not in scrape_failed_set
    ]

    return DisappearanceResult(
        is_first_run=is_first_run,
        added=_dedupe_keys(added),
        removed=_dedupe_keys(removed),
        stale_lingering=_dedupe_keys(stale_lingering),
        scrape_failed=_dedupe_keys(scrape_failed),
        status_changed=status_changed,
        prev_count=len(prev_snapshot),
        current_count=len(current_listings),
    )
