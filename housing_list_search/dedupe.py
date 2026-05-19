"""
Deduplication utilities for housing opportunity records.

When multiple sources (San José portal, SCCHA properties directory, Gilroy PDFs,
other county lists) are combined, the same physical property often appears in
more than one place. This module provides conservative, high-precision deduping
so the final CSV / daily deltas / public table stay clean and actionable.

Naming note: deduping is a cross-source concern, not tied to any one city or tool.
"""

from __future__ import annotations
from typing import List, Dict, Any, Tuple
import re


def _norm_name(name: str) -> str:
    """Aggressive but safe normalization for matching across sources."""
    if not name:
        return ""
    n = name.lower()

    # Remove very common varying suffixes (order matters — longer first)
    suffixes = [
        " senior apartments", " family apartments", " senior housing", " family housing",
        " apartments", " apartment", " housing", " homes", " village", " gardens",
        " court", " plaza", " park", " studios", " lofts", " way", " drive",
        " senior", " family"
    ]
    for s in suffixes:
        if n.endswith(s):
            n = n[: -len(s)]
        else:
            n = n.replace(s, " ")

    # Collapse multiple spaces and remove non-alphanum
    n = re.sub(r"\s+", " ", n)
    n = re.sub(r"[^a-z0-9]", "", n)
    return n.strip()


def _norm_address(addr: str) -> str:
    """Robust street-level key tolerant of real-world formatting differences."""
    if not addr:
        return ""
    a = addr.lower()

    # Normalize common street suffixes
    a = re.sub(r"\b(st|street)\b", "st", a)
    a = re.sub(r"\b(ave|avenue)\b", "ave", a)
    a = re.sub(r"\b(dr|drive)\b", "dr", a)
    a = re.sub(r"\b(rd|road)\b", "rd", a)
    a = re.sub(r"\b(blvd|boulevard)\b", "blvd", a)
    a = re.sub(r"\b(ln|lane)\b", "ln", a)
    a = re.sub(r"\b(ct|court)\b", "ct", a)
    a = re.sub(r"\b(pl|place)\b", "pl", a)
    a = re.sub(r"\b(way)\b", "way", a)

    # Capture street number + up to two following words (handles 'De Rose', 'South Bascom', 'Branham Lane')
    m = re.search(r"(\d{1,5})\s+([a-z][a-z0-9\-]*(?:\s+[a-z][a-z0-9\-]*)?)", a)
    if m:
        num = m.group(1)
        street = re.sub(r"\s+", "", m.group(2))[:18]
        return f"{num}{street}"

    # Fallback
    return re.sub(r"[^a-z0-9]", "", a)[:30]


def _make_key(rec: Dict[str, Any]) -> Tuple[str, str, str]:
    """Return (name_key, addr_key, addr_only_key).
    We treat a strong address match as sufficient for dedup even if names differ.
    """
    name_key = _norm_name(rec.get("property_name") or rec.get("name", ""))
    addr_key = _norm_address(rec.get("address") or rec.get("url", ""))
    # Pure address key for fallback (longer = more trustworthy)
    addr_only = addr_key if len(addr_key) >= 6 else ""
    return (name_key, addr_key, addr_only)


def deduplicate_listings(listings: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    """
    Remove duplicate properties across sources.

    Keeps the record that came from the "highest authority" source when possible
    (currently just the first one seen after a stable sort by confidence + source).
    Conservative: only drops obvious duplicates.
    """
    if not listings:
        return []

    # Prefer records with higher confidence and more complete contact info
    def score(rec):
        raw_c = rec.get("confidence", 0.5)
        if isinstance(raw_c, str):
            raw_c = {"high": 0.9, "medium": 0.7, "low": 0.4}.get(raw_c.lower(), 0.5)
        c = float(raw_c)
        has_contact = bool(rec.get("phone") or rec.get("email"))
        has_url = bool(rec.get("url") or rec.get("document_url"))
        return (c, has_contact, has_url)

    # Stable order: highest score first
    sorted_listings = sorted(listings, key=score, reverse=True)

    seen: set = set()
    unique: List[Dict[str, Any]] = []

    for rec in sorted_listings:
        name_k, addr_k, addr_only = _make_key(rec)

        # Primary key: prefer strong address when available
        primary = addr_only if addr_only else (name_k, addr_k)

        if primary in seen:
            continue

        seen.add(primary)
        # Also block on the composite to be extra safe
        if name_k and addr_k:
            seen.add((name_k, addr_k))
        unique.append(rec)

    dropped = len(listings) - len(unique)
    if dropped > 0:
        print(f"   [dedupe] Removed {dropped} duplicate properties across sources")
    return unique
