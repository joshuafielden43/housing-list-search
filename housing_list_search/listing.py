"""
listing.py — canonical Listing shape at the persistence seam.

Adapters return plain dicts or HousingRecord dataclasses with varying keys.
listing_to_row() is the single coercion path used by db.upsert_listings(),
save_current_full(), and any future export surfaces.
"""

from __future__ import annotations

from datetime import datetime
from typing import Any

from housing_list_search.dedupe import _norm_address
from housing_list_search.status_labels import resolve_status_label

_SURROGATE_PREFIX = "hls:"


def persistence_url(raw: dict[str, Any]) -> str:
    """
    URL used for listing identity (DB unique key, changelog, freshness).

    When adapters have no per-property link, derive a stable surrogate so
    distinct records do not collide on empty url.
    """
    url = (raw.get("url") or raw.get("document_url") or "").strip()
    if url:
        return url

    address = (raw.get("address") or "").strip()
    addr_key = _norm_address(address)
    if addr_key and len(addr_key) >= 6 and any(c.isdigit() for c in addr_key):
        return f"{_SURROGATE_PREFIX}addr:{addr_key}"

    source_url = (raw.get("source_url") or "").strip()
    name = (raw.get("property_name") or "").strip()
    if source_url and name:
        return f"{_SURROGATE_PREFIX}src:{source_url}#{name}"
    if name:
        return f"{_SURROGATE_PREFIX}prop:{name}"
    return ""


def coerce_listing(item: Any) -> dict[str, Any]:
    """Normalize any adapter output to a plain dict."""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "to_dict"):
        return item.to_dict()
    return dict(vars(item))


def listing_to_row(item: Any, *, now: str | None = None) -> dict[str, Any]:
    """
    Convert adapter output to the canonical housing_records row shape.

    Returns a dict keyed for DB persistence. CSV export maps authority →
    source_authority at write time (see normalizer.save_current_full).
    """
    raw = coerce_listing(item)
    ts = now or datetime.now().isoformat()

    notes = (raw.get("notes") or "").strip()
    extra: list[str] = []
    if raw.get("address") and raw.get("address") not in notes:
        extra.append(f"addr: {raw['address']}")
    if raw.get("phone"):
        extra.append(f"phone: {raw['phone']}")
    if raw.get("email"):
        extra.append(f"email: {raw['email']}")
    if raw.get("bedrooms"):
        extra.append(f"br: {raw['bedrooms']}")
    if extra:
        notes = (notes + " | " + " | ".join(extra)).strip(" |")

    flags = raw.get("eligibility_flags") or []
    if isinstance(flags, list):
        eligibility_flags = "|".join(str(f) for f in flags)
    else:
        eligibility_flags = str(flags)

    authority = (raw.get("authority") or raw.get("source_authority") or "").strip()
    property_name = (raw.get("property_name") or "").strip()
    url = persistence_url(raw)

    return {
        "authority": authority,
        "property_name": property_name,
        "url": url,
        "address": (raw.get("address") or "").strip(),
        "phone": (raw.get("phone") or "").strip(),
        "email": (raw.get("email") or "").strip(),
        "deadline": (raw.get("deadline") or "").strip(),
        "bedrooms": str(raw.get("bedrooms") or "").strip(),
        "income_limits": str(raw.get("income_limits") or "").strip(),
        "unit_types": str(raw.get("unit_types") or raw.get("bedrooms") or "").strip(),
        "eligibility_flags": eligibility_flags,
        "status": resolve_status_label(raw),
        "listing_status": (raw.get("listing_status") or "").lower().strip(),
        "notes": notes,
        "confidence": str(raw.get("confidence") or "").strip(),
        "administrator": str(raw.get("administrator") or "").strip(),
        "administrator_url": str(raw.get("administrator_url") or "").strip(),
        "administrator_phone": str(raw.get("administrator_phone") or "").strip(),
        "administrator_contact": str(raw.get("administrator_contact") or "").strip(),
        "last_seen": raw.get("last_seen") or ts,
        "first_seen": raw.get("first_seen") or ts,
        "source": (raw.get("source") or "").strip(),
        "source_url": (raw.get("source_url") or raw.get("document_url") or "").strip(),
        "expires_at": (raw.get("expires_at") or "").strip(),
        "scrape_date": ts,
    }
