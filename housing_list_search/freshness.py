"""
freshness.py — compatibility shim.

All change semantics (machine Diff labels + staff Disappearance) live in
``disappearance.py``. This module re-exports the historical names so older
imports keep working.

New code should import from ``housing_list_search.disappearance``.
"""

from __future__ import annotations

from housing_list_search.disappearance import (  # noqa: F401
    ListingKey,
    RunDiff,
    compute_run_diff,
    key_from_diff_row,
    listing_identity,
    listings_by_key,
    load_diff_csv_rows,
)

__all__ = [
    "ListingKey",
    "RunDiff",
    "compute_run_diff",
    "key_from_diff_row",
    "listings_by_key",
    "listing_identity",
    "load_diff_csv_rows",
]
