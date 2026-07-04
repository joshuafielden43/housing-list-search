"""Shared listing_status → display status mapping for CSV, diff, and changelog."""

from __future__ import annotations

from typing import Any

_LISTING_STATUS_MAP = {
    "open": "Open",
    "waitlist": "Waitlist Open",
    "coming_soon": "Coming Soon",
    "closed": "Closed",
}


def resolve_status_label(item: dict[str, Any]) -> str:
    """Return the human-readable status label for a raw listing dict."""
    listing_status = (item.get("listing_status") or "").lower().strip()
    if listing_status in _LISTING_STATUS_MAP:
        return _LISTING_STATUS_MAP[listing_status]
    return (item.get("status") or "Unknown").strip() or "Unknown"
