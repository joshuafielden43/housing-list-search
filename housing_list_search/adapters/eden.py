"""
Eden Housing Adapter (First-Class, Company-Named)

Eden Housing (edenhousing.org) is a major nonprofit developer/manager with
~36 properties in Santa Clara County. theunitedeffort.org watches 31 of
their pages individually.

Data source: /about-us/all-properties/all-properties-list/ supports
server-rendered county filtering via the `_sft_county` query param
(Search & Filter WordPress plugin). The result grid (div.property-grid)
carries, per property: leasing status ("Accepting Applications" / "Closed"),
name, city, unit count, and the /properties/<slug>/ detail link.
One request covers the whole county portfolio.

KNOWN ENVIRONMENT QUIRK (2026-06-10): edenhousing.org's bare-domain DNS
fails (SERVFAIL) on some resolvers while public DNS (1.1.1.1, 8.8.8.8)
resolves it fine. If this adapter logs a DNS failure, switch the machine's
DNS to a public resolver — the site itself is up.

Public entry point:
    scrape_eden(authority, url)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime as _dt
from typing import Any

from bs4 import BeautifulSoup

from housing_list_search.scraper import polite_get

logger = logging.getLogger(__name__)

COUNTY_LIST_URL = (
    "https://edenhousing.org/about-us/all-properties/all-properties-list/?_sft_county=santa-clara"
)

_LISTING_STATUS = {
    "accepting applications": "open",
    "closed": "closed",
    "waitlist open": "waitlist",
    "coming soon": "coming_soon",
}


def parse_property_grid(html_text: str, now_iso: str, source_url: str) -> list[dict[str, Any]]:
    """Parse the Eden county-filtered property grid into records."""
    soup = BeautifulSoup(html_text, "html.parser")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    _STATUS_WORDS = {"accepting applications", "waitlist open", "coming soon", "closed"}

    # Group anchors by property URL — each card links to the same detail page
    # from its status badge, image, and title; only the title text is a name.
    by_href: dict[str, list] = {}
    for a in soup.find_all("a", href=re.compile(r"/properties/[^/]+/?$")):
        by_href.setdefault(a.get("href", ""), []).append(a)

    for href, anchors in by_href.items():
        name = next(
            (
                t
                for a in anchors
                if (t := a.get_text(strip=True)) and t.lower() not in _STATUS_WORDS
            ),
            "",
        )
        if not name or href in seen:
            continue

        # Card layout: status / name-link / "City, California" / unit count.
        # Walk up from the title anchor until the card text includes the city.
        title_a = next((a for a in anchors if a.get_text(strip=True) == name), anchors[0])
        card = title_a
        text = ""
        for _ in range(6):
            card = card.find_parent(["li", "div", "article"])
            if card is None:
                break
            text = card.get_text(" | ", strip=True)
            if ", California" in text:
                break

        # The status badge is itself one of the anchors pointing at this property.
        status = next(
            (t for a in anchors if (t := a.get_text(strip=True)) and t.lower() in _STATUS_WORDS),
            "",
        )
        if not status:
            # Badge may sit outside the city-level div — include one parent up.
            wider = (
                card.parent.get_text(" | ", strip=True)
                if card is not None and card.parent
                else text
            )
            for s in ("Accepting Applications", "Waitlist Open", "Coming Soon", "Closed"):
                if re.search(rf"\b{s}\b", wider, re.I):
                    status = s
                    break

        city = ""
        m = re.search(r"([A-Z][A-Za-z .]+),\s*California", text)
        if m:
            city = m.group(1).strip()

        units = ""
        m = re.search(r"\|\s*(\d{1,4})\s*(?:\||$)", text)
        if m:
            units = m.group(1)

        seen.add(href)
        records.append(
            {
                "authority": "Eden Housing (Santa Clara County portfolio)",
                "property_name": name,
                "address": f"{city}, CA" if city else "",
                "url": href,
                "status": status or "See property page",
                "listing_status": _LISTING_STATUS.get(status.lower(), ""),
                "unit_types": f"{units} units" if units else "",
                "administrator": "Eden Housing",
                "administrator_url": "https://edenhousing.org/",
                "notes": "Eden Housing managed property" + (f" | {units} units" if units else ""),
                "confidence": "high",
                "last_seen": now_iso,
                "first_seen": now_iso,
                "source": "eden:county_list",
                "source_url": source_url,
                "expires_at": "",
            }
        )

    return records


def scrape_eden(authority: str = "", url: str = "") -> list[dict[str, Any]]:
    """Public entry point. Single county-filtered request."""
    print("🧩 Running Eden Housing adapter (county-filtered property list)")
    now_iso = _dt.now().isoformat()
    target = url or COUNTY_LIST_URL

    from housing_list_search.scraper import require_response

    resp = polite_get(target)
    if not resp:
        logger.warning(
            "[eden] Could not fetch %s. If this is a DNS failure, note that "
            "edenhousing.org SERVFAILs on some resolvers — switch the machine "
            "to public DNS (1.1.1.1 / 8.8.8.8).",
            target,
        )
    resp = require_response(resp, target, context="eden")

    records = parse_property_grid(resp.text, now_iso, target)
    print(f"   → Eden Housing: {len(records)} Santa Clara County properties")
    return records
