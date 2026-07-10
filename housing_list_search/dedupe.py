"""
Deduplication utilities for housing opportunity records.

When multiple sources (San José portal, SCCHA properties directory, Gilroy PDFs,
other county lists) are combined, the same physical property often appears in
more than one place. Operates on canonical Listing rows (post listing_to_row).

Naming note: deduping is a cross-source concern, not tied to any one city or tool.
"""

from __future__ import annotations

from typing import Any

from housing_list_search.listing import (
    ListingKey,
    canonicalize_listings,
    cross_source_key,
    listing_identity,
)


def deduplicate_listings(
    listings: list[Any],
    *,
    canonical: bool = False,
) -> list[dict[str, Any]]:
    """
    Remove duplicate properties across sources on canonical Listing rows.

    Exact duplicates share a ListingKey (authority, property_name, url).
    Cross-source mirrors merge on shared hls:addr: URL or street-level address.

    Survivors are the only rows upserted with full content. Mirror identities
    dropped here are still *confirmed* for the run via
    ``DatabaseManager.confirm_listing_identities`` in the pipeline (#661 / #773)
    so a preferred authority does not false-STALE the other source's DB row.

    When canonical=False, listing_to_row() runs first (backward-compatible entry).
    """
    if not listings:
        return []

    rows = listings if canonical else canonicalize_listings(listings)

    def score(rec: dict[str, Any]) -> tuple[float, bool, bool]:
        raw_c = rec.get("confidence", 0.5)
        if isinstance(raw_c, str):
            raw_c = {"high": 0.9, "medium": 0.7, "low": 0.4}.get(raw_c.lower(), 0.5)
        c = float(raw_c)
        has_contact = bool(rec.get("phone") or rec.get("email"))
        has_url = bool(rec.get("url"))
        return (c, has_contact, has_url)

    sorted_rows = sorted(rows, key=score, reverse=True)

    seen_identity: set[ListingKey] = set()
    seen_cross: set[tuple[str, str]] = set()
    unique: list[dict[str, Any]] = []

    for rec in sorted_rows:
        ident = listing_identity(rec)
        cross = cross_source_key(rec)

        if ident in seen_identity:
            continue
        if cross and cross in seen_cross:
            continue

        seen_identity.add(ident)
        if cross:
            seen_cross.add(cross)
        unique.append(rec)

    dropped = len(rows) - len(unique)
    if dropped > 0:
        print(f"   [dedupe] Removed {dropped} duplicate properties across sources")
    return unique
