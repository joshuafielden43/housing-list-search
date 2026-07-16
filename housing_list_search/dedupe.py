"""
Deduplication utilities for housing opportunity records.

When multiple sources (San José portal, SCCHA properties directory, Gilroy PDFs,
other county lists) are combined, the same physical property often appears in
more than one place. Operates on canonical Listing rows (post listing_to_row).

Cross-source mirror confirm (#661 / #773 / #1071): survivors are content-upserted;
dropped identities still seen this run are returned for last_run_id confirm so
they are not false-STALE.

Naming note: deduping is a cross-source concern, not tied to any one city or tool.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Any

from housing_list_search.listing import canonicalize_listings
from housing_list_search.listing_identity import (
    ListingKey,
    cross_source_key,
    mirror_confirm_keys,
    persistence_key,
)

logger = logging.getLogger(__name__)


@dataclass(frozen=True)
class DedupeResult:
    """Survivors for content upsert + mirror identities still seen this run.

    mirrors_to_confirm: ListingKeys present in the pre-dedupe set but not among
    survivors (cross-source losers, or same physical property under another
    authority). Machine Persist passes these to confirm_listing_identities.
    """

    survivors: list[dict[str, Any]]
    mirrors_to_confirm: frozenset[ListingKey]


def deduplicate_for_run(
    listings: list[Any],
    *,
    canonical: bool = False,
) -> DedupeResult:
    """
    Cross-source dedupe with explicit mirror set for run confirmation.

    Exact duplicates share a ListingKey (authority, property_name, url).
    Cross-source mirrors merge on shared hls:addr: URL or street-level address.

    Survivors are the only rows content-upserted. Mirror identities dropped
    here must still be confirmed for the run (#661 / #773) so a preferred
    authority does not false-STALE the other source's DB row.

    When canonical=False, listing_to_row() runs first (backward-compatible entry).
    """
    if not listings:
        return DedupeResult(survivors=[], mirrors_to_confirm=frozenset())

    rows = listings if canonical else canonicalize_listings(listings)
    all_identities = {persistence_key(r) for r in rows}

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
        ident = persistence_key(rec)
        cross = cross_source_key(rec)

        if ident in seen_identity:
            continue
        if cross and cross in seen_cross:
            continue

        seen_identity.add(ident)
        if cross:
            seen_cross.add(cross)
        unique.append(rec)

    survivor_ids = {persistence_key(r) for r in unique}
    mirrors = mirror_confirm_keys(all_identities, survivor_ids)

    dropped = len(rows) - len(unique)
    if dropped > 0:
        logger.info(
            "dedupe: removed %d duplicate propert%s across sources (%d mirror identities to confirm)",
            dropped,
            "y" if dropped == 1 else "ies",
            len(mirrors),
        )
    return DedupeResult(survivors=unique, mirrors_to_confirm=mirrors)


def deduplicate_listings(
    listings: list[Any],
    *,
    canonical: bool = False,
) -> list[dict[str, Any]]:
    """Return survivor rows only (compat wrapper around deduplicate_for_run)."""
    return deduplicate_for_run(listings, canonical=canonical).survivors
