"""
listing.py — canonical Listing shape at the persistence seam.

The deep module for turning raw adapter output into canonical Listings.
listing_to_row() (and canonicalize_listings) is the single coercion path.
Surrogate URLs and authority canon for row shape live here.

Listing Identity (persistence_key, cross_source_key, confirm match policy)
lives in listing_identity.py — not here.
"""

from __future__ import annotations

import hashlib
import logging
import re
from datetime import datetime
from typing import Any

from housing_list_search.status_labels import resolve_status_label

logger = logging.getLogger(__name__)

# Canonical row shape is a plain dict from listing_to_row() — not a parallel
# value type. #1062 deleted CanonicalListing (half-depth: built only to to_dict()).

_SURROGATE_PREFIX = "hls:"


def canonical_authority(auth: str) -> str:
    """Map known authority variants to a stable canonical label for identity keys.

    This prevents NEW/STALE churn and duplicate records when TARGETS.md uses
    descriptive names (e.g. "John Stewart Company (jsco.net portfolio)") while
    adapters or other sources emit slightly different strings for the same vendor.
    All normalization for the (authority, ...) identity key lives here.

    Expanded for #983 to cover common Housing Group, SCCHA, etc. variants seen in practice.
    """
    a = (auth or "").strip()
    if not a:
        return ""
    low = a.lower()

    if low.startswith("john stewart") or "john stewart company" in low:
        return "John Stewart Company"
    if "sccha" in low or "santa clara county housing authority" in low:
        return "Santa Clara County Housing Authority"
    if "housing group" in low:
        return "Housing Group"
    if "midpen" in low:
        return "MidPen Housing"
    if "charities housing" in low:
        return "Charities Housing"
    if "eden housing" in low:
        return "Eden Housing"
    if "eah housing" in low or "eah" == low.split()[0]:
        return "EAH Housing"
    # Add other known vendor/platform aliases here as needed (document in AGENTS.md).
    return a


def norm_property_name(name: str) -> str:
    """Aggressive name normalization for cross-source matching (#797)."""
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


def norm_address(addr: str) -> str:
    """Robust street-level key tolerant of real-world formatting differences.

    Improvements for seam stability (see #983):
    - More suffix normalizations.
    - Capture more of street name.
    - Fall back to fuller alphanum but cap reasonably.
    - This reduces (but does not eliminate) collision risk for surrogates.
    """
    if not addr:
        return ""
    a = addr.lower()

    # Common street suffix normalizations
    suffixes = {
        r"\b(street|st\.?)\b": "st",
        r"\b(avenue|ave\.?)\b": "ave",
        r"\b(drive|dr\.?)\b": "dr",
        r"\b(road|rd\.?)\b": "rd",
        r"\b(boulevard|blvd\.?)\b": "blvd",
        r"\b(lane|ln\.?)\b": "ln",
        r"\b(court|ct\.?)\b": "ct",
        r"\b(place|pl\.?)\b": "pl",
        r"\b(way)\b": "way",
        r"\b(circle|cir\.?)\b": "cir",
        r"\b(terrace|ter\.?)\b": "ter",
    }
    for pat, repl in suffixes.items():
        a = re.sub(pat, repl, a)

    # Try to capture number + street
    m = re.search(r"(\d{1,6})\s+([a-z][a-z0-9\-\s]{0,40})", a)
    if m:
        num = m.group(1)
        street = re.sub(r"[^a-z0-9]", "", m.group(2).strip())[:25]
        if street:
            return f"{num}{street}"

    # Fallback: clean alphanum, longer cap for better distinction
    cleaned = re.sub(r"[^a-z0-9]", "", a)
    return cleaned[:40]


def _persistence_url(raw: dict[str, Any]) -> str:
    """
    URL used for listing identity (DB unique key, changelog, Disappearance).

    When adapters have no per-property link, derive a stable surrogate so
    distinct records do not collide on empty url.

    Improvements (#983): scope prop/src surrogates with authority slug + short
    hash of key fields to reduce cross-authority or same-name collisions.
    Prefer address surrogate when possible.
    """
    url = (raw.get("url") or raw.get("document_url") or "").strip()
    if url:
        return url

    address = (raw.get("address") or "").strip()
    addr_key = norm_address(address)
    if addr_key and len(addr_key) >= 6 and any(c.isdigit() for c in addr_key):
        return f"{_SURROGATE_PREFIX}addr:{addr_key}"

    auth = canonical_authority(raw.get("authority") or raw.get("source_authority") or "")
    auth_slug = re.sub(r"[^a-z0-9]", "", auth.lower())[:20]
    source_url = (raw.get("source_url") or "").strip()
    name = (raw.get("property_name") or "").strip()

    # Build disambiguating suffix from available data
    disambig = name or ""
    if address:
        disambig = f"{disambig}:{norm_address(address)[:15]}"
    if source_url:
        disambig = f"{disambig}:{source_url[-20:]}"

    # Simple hash for extra uniqueness when needed (short, stable)
    if disambig:
        h = hashlib.sha256(disambig.encode("utf-8")).hexdigest()[:8]
        if source_url and name:
            return f"{_SURROGATE_PREFIX}src:{auth_slug}:{h}"
        if name:
            return f"{_SURROGATE_PREFIX}prop:{auth_slug}:{h}"

    if auth_slug:
        return f"{_SURROGATE_PREFIX}prop:{auth_slug}:unknown"
    return ""


def _canon_auth_name(raw: dict[str, Any]) -> tuple[str, str]:
    """Shared: canonical authority + stripped property_name for both row and identity."""
    auth = canonical_authority(raw.get("authority") or raw.get("source_authority") or "")
    name = (raw.get("property_name") or "").strip()
    return auth, name


def canonicalize_listings(
    listings: list[Any],
    *,
    now: str | None = None,
) -> list[dict[str, Any]]:
    """Apply listing_to_row() to every adapter record before dedupe or identity checks."""
    out: list[dict[str, Any]] = []
    dropped = 0
    for item in listings:
        row = listing_to_row(item, now=now)
        if row.get("authority") and row.get("property_name"):
            out.append(row)
        else:
            dropped += 1
            logger.warning(
                "canonicalize_listings dropped row without authority or property_name: "
                "authority=%r property_name=%r raw_keys=%s",
                row.get("authority"),
                row.get("property_name"),
                list(coerce_listing(item).keys()) if item else [],
            )
    if dropped:
        logger.warning("canonicalize_listings dropped %d incomplete record(s) — see above", dropped)
    return out


def coerce_listing(item: Any) -> dict[str, Any]:
    """Normalize any adapter output to a plain dict."""
    if isinstance(item, dict):
        return dict(item)
    if hasattr(item, "to_dict"):
        return item.to_dict()
    return dict(vars(item))


def coerce_adapter_records(raw: list[Any]) -> list[dict[str, Any]]:
    """Normalize a list of adapter/extractor items to plain dicts (#801).

    Uses coerce_listing per item so HousingRecord and dicts share one path.
    Dispatch measure handlers and URL extractors must pass through here.
    """
    return [coerce_listing(item) for item in (raw or [])]


def listing_to_row(item: Any, *, now: str | None = None) -> dict[str, Any]:
    """
    Convert adapter output to the canonical housing_records row shape.

    Returns a dict keyed for DB persistence (single coercion path).
    Production CSV uses db.export_csv() (which projects source_authority).

    Idempotent on already-canonical rows.
    """
    if isinstance(item, dict) and item.get("scrape_date"):
        # already canonical
        return dict(item)
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

    authority, property_name = _canon_auth_name(raw)
    url = _persistence_url(raw)

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
