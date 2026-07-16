"""
Alta Housing Adapter — First-Class (Company-Named)

Alta Housing (altahousing.org) is a long-standing third-party administrator
for Below Market Rate (BMR) programs, most notably for the City of Palo Alto
(and potentially other jurisdictions over time).

This is the reference adapter for the "Alta Housing" delegated administrator pattern.

Core inventory path (#1078):
- Per-property cards from altahousing.org/current-properties/ (static HTML).
- City BMR program pages are NOT scraped for synthetic "ownership/rental/map"
  property rows — those heuristics produced wrong apply URLs and inflated inventory.

Scope & Guardrails
------------------
In Scope:
- Extracting property name, address, waitlist status, detail URL from the directory.
- Returning records with freshness metadata for delta processing.

Out of Scope:
- Fabricating program-level placeholder "listings" from city housing pages.
- Creating accounts or submitting applications.
- Solving CAPTCHAs or login walls.
"""

from __future__ import annotations

import logging
from datetime import datetime as _dt
from typing import Any

from bs4 import BeautifulSoup

from housing_list_search.access import polite_get

logger = logging.getLogger(__name__)

# Alta's own property directory — a static WordPress grid of .prop-box cards,
# each carrying name, full address, waitlist status, and a detail-page link.
# This is the per-property availability signal (theunitedeffort.org watches
# these same pages for staleness).
PROPERTY_DIRECTORY_URL = "https://altahousing.org/current-properties/"

# .prop-flag badge text → our listing_status vocabulary (status_labels.py)
_STATUS_MAP = {
    "waitlist open": "waitlist",
    "waitlist closed": "closed",
    "available": "open",
    "now leasing": "open",
    "coming soon": "coming_soon",
}


def scrape_alta(authority: str, url: str, timeout: int = 60000) -> list[dict[str, Any]]:
    """
    Public entry point for the Alta Housing adapter.

    ``url`` is accepted for dispatch uniformity (city BMR page from TARGETS.md)
    but inventory comes only from the Alta property directory (#1078).
    ``timeout`` is unused (kept for call-site compatibility).
    """
    del url, timeout  # dispatch always passes these; inventory is directory-only
    logger.info("[alta] Starting scrape_alta for %s (property directory)", authority)

    records = scrape_property_directory(authority)
    if not records:
        from housing_list_search.access import SourceFetchError

        # Directory fetch succeeded but yielded no cards — soft success kills
        # inventory via STALE. Fail the authority instead (#1076).
        raise SourceFetchError(
            f"alta: property directory returned zero properties for {authority}"
        )

    logger.info("[alta] Extracted %d directory properties for %s", len(records), authority)
    return records


def run(ctx) -> list[dict[str, Any]]:
    """Adapter port: TargetContext → records (dispatch Handler)."""
    return scrape_alta(ctx.authority, ctx.url)


def scrape_property_directory(authority: str = "") -> list[dict[str, Any]]:
    """Parse altahousing.org/current-properties/ into per-property records.

    Each .prop-box card yields property name, full street address (with city),
    waitlist status, and the property detail URL. The directory spans every
    city Alta operates in (Palo Alto, Mountain View, Redwood City), so the
    city lives in the address while `authority` records which TARGETS.md row
    triggered the scrape.
    """
    from housing_list_search.access import require_response

    resp = require_response(
        polite_get(PROPERTY_DIRECTORY_URL),
        PROPERTY_DIRECTORY_URL,
        context="alta",
    )

    soup = BeautifulSoup(resp.text, "html.parser")
    now_iso = _dt.now().isoformat()
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for box in soup.find_all("div", class_="prop-box"):
        title_link = box.select_one(".prop-title a") or box.find("a", href=True)
        if not title_link:
            continue
        name = title_link.get_text(strip=True)
        detail_url = title_link.get("href", "")
        if not name or detail_url in seen_urls:
            continue
        seen_urls.add(detail_url)

        flag = box.select_one(".prop-flag")
        status_text = flag.get_text(strip=True) if flag else ""
        listing_status = _STATUS_MAP.get(status_text.lower(), "")

        desc = box.select_one(".block-desc")
        address = desc.get_text(" ", strip=True) if desc else ""

        records.append(
            {
                "authority": authority or "Alta Housing",
                "property_name": name,
                "address": address,
                "url": detail_url,
                "status": status_text or "See property page",
                "listing_status": listing_status,
                "administrator": "Alta Housing",
                "administrator_url": "https://altahousing.org/",
                "administrator_phone": "(650) 321-9709",
                "notes": f"Alta Housing managed property | directory status: {status_text}",
                "confidence": "high",
                "last_seen": now_iso,
                "first_seen": now_iso,
                "source": "alta:property_directory",
                "source_url": PROPERTY_DIRECTORY_URL,
                "expires_at": "",
            }
        )

    logger.info("[alta] Property directory: %d properties", len(records))
    return records
