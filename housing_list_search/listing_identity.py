"""
listing_identity.py — Listing Identity deep module.

Owns persistence keys, cross-source merge keys, and pure confirm/match policy
so Machine Persist and Disappearance share one contract for "same row" vs
"same physical property" vs "touch these keys so they are not false-STALE".

Does not classify STALE/REMOVED (Disappearance) and does not execute SQL
(Inventory Store only touches keys this module produces or matches).

Public interface:
  persistence_key(row) → ListingKey
  cross_source_key(row) → merge key | None
  mirror_confirm_keys(all_run, survivors) → frozenset[ListingKey]
  alias_matches(survivor, candidate) → bool  (#1104 rules as-is; tighten later)
"""

from __future__ import annotations

from collections.abc import Collection, Iterable
from typing import Any

from housing_list_search.listing import (
    listing_to_row,
    norm_address,
    norm_property_name,
)

# (authority, property_name, url) — DB unique key and Disappearance identity
ListingKey = tuple[str, str, str]

__all__ = [
    "ListingKey",
    "persistence_key",
    "cross_source_key",
    "mirror_confirm_keys",
    "alias_matches",
]


def persistence_key(item: Any) -> ListingKey:
    """DB / Disappearance key: (authority, property_name, url).

    Idempotent on already-canonical rows (trusts their authority/url).
    Non-canonical adapter dicts pass through listing_to_row for surrogates.
    """
    if isinstance(item, dict) and item.get("scrape_date"):
        auth = item.get("authority") or ""
        name = (item.get("property_name") or "").strip()
        url = (item.get("url") or "").strip()
        return auth, name, url

    row = listing_to_row(item)
    return (
        row.get("authority") or "",
        (row.get("property_name") or "").strip(),
        (row.get("url") or "").strip(),
    )


def cross_source_key(row: dict[str, Any]) -> tuple[str, str] | None:
    """
    Key for merging the same physical property across authorities (#797).

    Prefer shared hls:addr: surrogates; else street-level address; else name+addr.
    """
    url = (row.get("url") or "").strip()
    if url.startswith("hls:addr:"):
        return ("url", url)

    addr_key = norm_address(row.get("address") or "")
    if len(addr_key) >= 6 and any(c.isdigit() for c in addr_key):
        return ("addr", addr_key)

    name_key = norm_property_name(row.get("property_name") or "")
    if name_key and addr_key:
        return ("name_addr", f"{name_key}:{addr_key}")

    return None


def mirror_confirm_keys(
    all_run_identities: Collection[ListingKey],
    survivor_identities: Collection[ListingKey],
) -> frozenset[ListingKey]:
    """Identities seen this run but not content-upserted survivors (#661 / #1071).

    Machine Persist passes the result to Store.confirm_listing_identities.
    """
    return frozenset(all_run_identities) - frozenset(survivor_identities)


def alias_matches(survivor: dict[str, Any], candidate: dict[str, Any]) -> bool:
    """True if candidate is another authority/url row for the same property (#1104).

    Behaviour-preserving lift of confirm_property_aliases SQL predicates:
    same property_name and (same url OR same raw address with len >= 8),
    and authority differs when survivor authority is non-empty.
    Tighten with fixtures later — do not invent new match policy here.
    """
    s_name = (survivor.get("property_name") or "").strip()
    c_name = (candidate.get("property_name") or "").strip()
    if not s_name or s_name != c_name:
        return False

    s_auth = (survivor.get("authority") or "").strip()
    c_auth = (candidate.get("authority") or "").strip()
    if s_auth and c_auth and s_auth == c_auth:
        return False

    s_url = (survivor.get("url") or "").strip()
    c_url = (candidate.get("url") or "").strip()
    if s_url and s_url == c_url:
        return True

    s_addr = (survivor.get("address") or "").strip()
    c_addr = (candidate.get("address") or "").strip()
    if s_addr and len(s_addr) >= 8 and s_addr == c_addr:
        return True

    return False


def keys_from_rows(rows: Iterable[dict[str, Any]]) -> frozenset[ListingKey]:
    """Convenience: persistence keys for a set of canonical (or raw) rows."""
    return frozenset(persistence_key(r) for r in rows)
