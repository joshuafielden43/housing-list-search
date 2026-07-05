"""
disappearance.py — project staff-facing disappearance from diff.csv (ADR-0001).

run_prev.csv is used only for STATUS_CHANGE (display status run-over-run).
ADDED, REMOVED, STALE, and SCRAPE_FAILED project from diff.csv rows.
"""

from __future__ import annotations

from dataclasses import dataclass, field

from housing_list_search.freshness import ListingKey, compute_run_diff, listing_identity


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


def _key_from_diff_row(row: dict[str, str]) -> ListingKey:
    return listing_identity(
        {
            "authority": row.get("source_authority") or row.get("authority") or "",
            "property_name": row.get("property_name") or "",
            "url": row.get("url") or "",
        }
    )


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
        key = _key_from_diff_row(row)

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
