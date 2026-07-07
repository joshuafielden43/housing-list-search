"""
Deduplication utilities for housing opportunity records.

When multiple sources (San José portal, SCCHA properties directory, Gilroy PDFs,
other county lists) are combined, the same physical property often appears in
more than one place. Operates on canonical Listing rows (post listing_to_row).

Naming note: deduping is a cross-source concern, not tied to any one city or tool.
"""

from __future__ import annotations

import re
from typing import Any

from housing_list_search.listing import (
    ListingKey,
    canonicalize_listings,
    listing_identity,
    norm_address,
)

_CROSS_URL_PREFIX = "hls:addr:"


def _norm_name(name: str) -> str:
    """Aggressive but safe normalization for matching across sources."""
    if not name:
        return ""
    n = name.lower()

    suffixes = [
        " senior apartments",
        " family apartments",
        " senior housing",
        " family housing",
        " apartments",
        " apartment",
        " housing",
        " homes",
        " village",
        " gardens",
        " court",
        " plaza",
        " park",
        " studios",
        " lofts",
        " way",
        " drive",
        " senior",
        " family",
    ]
    for s in suffixes:
        if n.endswith(s):
            n = n[: -len(s)]
        else:
            n = n.replace(s, " ")

    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"[^a-z0-9]", "", n)
    return n.strip()


def _cross_source_key(row: dict[str, Any]) -> tuple[str, str] | None:
    """
    Key for merging the same physical property across authorities.

    Canonical rows share hls:addr: URLs when street addresses match; otherwise
    fall back to normalized street number + name pairing.
    """
    url = (row.get("url") or "").strip()
    if url.startswith(_CROSS_URL_PREFIX):
        return ("url", url)

    addr_key = norm_address(row.get("address") or "")
    if len(addr_key) >= 6 and any(c.isdigit() for c in addr_key):
        return ("addr", addr_key)

    name_key = _norm_name(row.get("property_name") or "")
    if name_key and addr_key:
        return ("name_addr", f"{name_key}:{addr_key}")

    return None


def deduplicate_listings(
    listings: list[Any],
    *,
    canonical: bool = False,
) -> list[dict[str, Any]]:
    """
    Remove duplicate properties across sources on canonical Listing rows.

    Exact duplicates share a ListingKey (authority, property_name, url).
    Cross-source mirrors merge on shared hls:addr: URL or street-level address.

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
        cross = _cross_source_key(rec)

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
