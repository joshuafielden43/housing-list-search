"""
MidPen Housing Adapter (First-Class, Company-Named)

MidPen Housing (midpen-housing.org) is one of the largest nonprofit
developers/managers in the Bay Area, with ~44 properties in Santa Clara
County. theunitedeffort.org watches 44+ of their pages individually.

Data source: the /find-housing/ search page (WordPress + Elementor + Ajax
Search Pro) supports server-rendered county filtering via query params.
Each result card (div.elementor-location-single) carries:

  - leasing status   ("Wait List Open", "Wait List Closed", "Referral Only",
                      "Interest List", "Coming Soon")
  - population type  ("Family", "Senior", "Supportive", combinations)
  - property name + /property/<slug>/ detail link
  - city ("San Jose, CA" trailing the description)

The county filter is applied server-side, so two paginated requests cover
the whole Santa Clara County portfolio. The leasing status is the freshness
signal that manual data-maintenance pipelines struggle to keep current.

Public entry point:
    scrape_midpen(authority, url)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime as _dt
from typing import Any

from bs4 import BeautifulSoup

from housing_list_search.scraper import polite_get

logger = logging.getLogger(__name__)

# Server-rendered Santa Clara County filter (same query theunitedeffort.org
# watches). {page} is the path-based pagination slot ("" for page 1, "2/" ...).
SEARCH_URL_TEMPLATE = (
    "https://www.midpen-housing.org/find-housing/{page}"
    "?p_asid=1&p_asp_data=1&current_page_id=23&qtranslate_lang=0"
    "&filters_changed=1&filters_initial=0"
    "&aspf%5Bleasing_status__1%5D=&aspf%5Btype__2%5D=&aspf%5Bcity__3%5D="
    "&aspf%5Bcounty__4%5D=Santa%20Clara"
    "&asp_gen%5B%5D=title&customset%5B%5D=properties&asp_ls="
)
MAX_PAGES = 4  # ~30 cards/page; county portfolio fits in 2 today

_STATUS_FLAGS = [
    "Wait List Open",
    "Wait List Closed",
    "Waitlist Open",
    "Waitlist Closed",
    "Referral Only",
    "Interest List",
    "Coming Soon",
    "Now Leasing",
]

# status flag → our listing_status vocabulary (status_labels.py)
_LISTING_STATUS = {
    "wait list open": "waitlist",
    "waitlist open": "waitlist",
    "now leasing": "open",
    "interest list": "waitlist",
    "coming soon": "coming_soon",
    "wait list closed": "closed",
    "waitlist closed": "closed",
    "referral only": "closed",
}

_POPULATION_TYPES = ["Family", "Senior", "Supportive"]

_CITY_RE = re.compile(r"([A-Z][A-Za-z .]+),\s*CA\s*$")


def _parse_card(card, now_iso: str, page_url: str) -> dict[str, Any] | None:
    links = card.find_all("a", href=re.compile(r"/property/[^/]+/?$"))
    if not links:
        return None
    # Page-level wrappers match the same class as property cards but contain
    # links to many properties — only true cards link to exactly one.
    if len({a["href"] for a in links}) > 1:
        return None
    # The first /property/ link in a card usually wraps the thumbnail image
    # (no text); the title link carries the property name.
    link = next((a for a in links if a.get_text(strip=True)), links[0])
    name = link.get_text(strip=True)
    if not name:
        # Derive from the slug: /property/arbor-park/ → "Arbor Park"
        slug = link["href"].rstrip("/").rsplit("/", 1)[-1]
        name = slug.replace("-", " ").title()
    if not name:
        return None

    text = card.get_text(" | ", strip=True)

    status = next((f for f in _STATUS_FLAGS if f in text), "")
    populations = ", ".join(p for p in _POPULATION_TYPES if re.search(rf"\b{p}\b", text))

    city = ""
    m = _CITY_RE.search(text)
    if m:
        city = m.group(1).strip()

    notes_bits = ["MidPen Housing managed property"]
    if populations:
        notes_bits.append(f"population: {populations}")
    if status == "Referral Only":
        notes_bits.append("units filled by agency referral only (e.g. Coordinated Entry)")

    return {
        "authority": "MidPen Housing (Santa Clara County portfolio)",
        "property_name": name,
        "address": f"{city}, CA" if city else "",
        "url": link.get("href", ""),
        "status": status or "See property page",
        "listing_status": _LISTING_STATUS.get(status.lower(), ""),
        "eligibility_flags": [p.lower() for p in _POPULATION_TYPES if p in populations],
        "administrator": "MidPen Housing",
        "administrator_url": "https://www.midpen-housing.org/",
        "notes": " | ".join(notes_bits),
        "confidence": "high",
        "last_seen": now_iso,
        "first_seen": now_iso,
        "source": "midpen:find_housing",
        "source_url": page_url,
        "expires_at": "",
    }


def scrape_midpen(authority: str = "", url: str = "") -> list[dict[str, Any]]:
    """Public entry point. Walks the county-filtered search pages."""
    print("🧩 Running MidPen adapter (county-filtered find-housing search)")
    now_iso = _dt.now().isoformat()
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for page_num in range(1, MAX_PAGES + 1):
        page_slot = "" if page_num == 1 else f"{page_num}/"
        page_url = SEARCH_URL_TEMPLATE.format(page=page_slot)
        resp = polite_get(page_url)
        if not resp:
            break

        soup = BeautifulSoup(resp.text, "html.parser")
        cards = soup.find_all("div", class_=lambda c: c and "elementor-location-single" in c)

        new_on_page = 0
        for card in cards:
            rec = _parse_card(card, now_iso, page_url)
            if rec and rec["url"] not in seen_urls:
                seen_urls.add(rec["url"])
                records.append(rec)
                new_on_page += 1

        if new_on_page == 0:
            break

    print(f"   → MidPen: {len(records)} Santa Clara County properties")
    return records
