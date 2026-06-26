"""
Charities Housing Adapter (First-Class, Company-Named)

Charities Housing (charitieshousing.org) is a nonprofit developer/manager of
affordable housing concentrated in Santa Clara County (~34 properties as of
June 2026). theunitedeffort.org watches 33 of their pages individually.

Two complementary sources on the same WordPress site, both cheap:

1. /find-a-home/ — the "Accepting Applications" directory. Static cards
   (div.h_apart_ctc) with property name, street address, per-property email,
   phone, unit types, and the detail-page link. This is the actionable,
   current-availability list (~17 properties).

2. /wp-json/wp/v2/property — the standard WordPress REST API listing the full
   portfolio (including properties not currently accepting applications),
   with last-modified timestamps. Used to backfill portfolio coverage so the
   record set spans all properties, not just open ones.

Both are fetched with polite_get (robots.txt check + delay); the whole
adapter costs two HTTP requests per run.

Public entry point:
    scrape_charities_housing(authority, url)
"""

from __future__ import annotations

import html as _html
import logging
import re
from datetime import datetime as _dt
from typing import Any, Dict, List

from bs4 import BeautifulSoup

from housing_list_search.scraper import polite_get

logger = logging.getLogger(__name__)

FIND_A_HOME_URL = "https://charitieshousing.org/find-a-home/"
API_URL = "https://charitieshousing.org/wp-json/wp/v2/property?per_page=100"

ADMINISTRATOR = "Charities Housing"
ADMINISTRATOR_URL = "https://charitieshousing.org/"


def _base_record(now_iso: str, method: str, source_url: str) -> Dict[str, Any]:
    return {
        "authority": "Charities Housing (Santa Clara County portfolio)",
        "administrator": ADMINISTRATOR,
        "administrator_url": ADMINISTRATOR_URL,
        "confidence": "high",
        "last_seen": now_iso,
        "first_seen": now_iso,
        "source": f"charities_housing:{method}",
        "source_url": source_url,
        "expires_at": "",
    }


def _parse_find_a_home(html_text: str, now_iso: str) -> List[Dict[str, Any]]:
    """Parse the div.h_apart_ctc directory cards on /find-a-home/."""
    soup = BeautifulSoup(html_text, "html.parser")
    records: List[Dict[str, Any]] = []

    for card in soup.find_all("div", class_="h_apart_ctc"):
        title_link = card.select_one(".heading_h4 a") or card.find("a", href=True)
        if not title_link:
            continue
        name = title_link.get_text(strip=True)
        detail_url = title_link.get("href", "")
        if not name:
            continue

        email = phone = address = ""
        for a in card.find_all("a", href=True):
            href = a["href"]
            if href.startswith("mailto:"):
                email = href[len("mailto:"):].strip()
            elif href.startswith("tel:"):
                phone = a.get_text(strip=True)
            elif "clipboard" in href or href == "javascript:;":
                address = a.get_text(" ", strip=True)

        unit_p = card.select_one(".unit_type_head p")
        unit_types = unit_p.get_text(" ", strip=True) if unit_p else ""

        rec = _base_record(now_iso, "find_a_home", FIND_A_HOME_URL)
        rec.update({
            "property_name": name,
            "address": re.sub(r"\s*,?\s*USA$", "", address).strip(),
            "email": email,
            "phone": phone,
            "unit_types": unit_types,
            "bedrooms": unit_types,
            "url": detail_url,
            "status": "Accepting Applications",
            "listing_status": "open",
            "notes": "Listed on Charities Housing 'Find A Home' (accepting applications) page",
        })
        records.append(rec)

    return records


def _fetch_portfolio_api(now_iso: str, known_urls: set[str]) -> List[Dict[str, Any]]:
    """Backfill the full portfolio from the WordPress REST API."""
    resp = polite_get(API_URL)
    if not resp:
        return []
    try:
        items = resp.json()
    except Exception:
        logger.warning("[charities_housing] API returned non-JSON")
        return []
    if not isinstance(items, list):
        return []

    records: List[Dict[str, Any]] = []
    for item in items:
        link = item.get("link") or ""
        if link in known_urls:
            continue  # already covered with richer data from /find-a-home/
        name = _html.unescape((item.get("title") or {}).get("rendered", "")).strip()
        if not name:
            continue
        modified = (item.get("modified") or "")[:10]
        taxonomy = [
            c.replace("home_taxonomy-", "").replace("-", " ")
            for c in item.get("class_list", [])
            if c.startswith("home_taxonomy-")
        ]

        rec = _base_record(now_iso, "portfolio_api", API_URL)
        rec.update({
            "property_name": name,
            "url": link,
            "status": "Not currently accepting applications",
            "listing_status": "closed",
            "notes": (
                "Charities Housing portfolio property (not on the current "
                "'Find A Home' list)"
                + (f" | category: {', '.join(taxonomy)}" if taxonomy else "")
                + (f" | vendor page last updated {modified}" if modified else "")
            ),
        })
        records.append(rec)

    return records


def scrape_charities_housing(authority: str = "", url: str = "") -> List[Dict[str, Any]]:
    """Public entry point. `url` is accepted for runner uniformity; the
    adapter always reads the two canonical charitieshousing.org sources."""
    print(f"🧩 Running Charities Housing adapter (find-a-home + portfolio API)")
    now_iso = _dt.now().isoformat()

    records: List[Dict[str, Any]] = []
    resp = polite_get(url or FIND_A_HOME_URL)
    if resp:
        records.extend(_parse_find_a_home(resp.text, now_iso))
    else:
        logger.warning("[charities_housing] Could not fetch %s", url or FIND_A_HOME_URL)

    known_urls = {r.get("url", "") for r in records}
    records.extend(_fetch_portfolio_api(now_iso, known_urls))

    print(f"   → Charities Housing: {len(records)} properties "
          f"({len(known_urls)} accepting applications)")
    return records
