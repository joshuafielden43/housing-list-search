"""
EAH Housing Adapter (First-Class, Company-Named)

EAH Housing (eahhousing.org) is a large nonprofit manager operating in
California and Hawaii (~173 properties total, ~30 in Santa Clara County).
theunitedeffort.org watches 30 of their pages individually.

Data source: the all-properties search results page (an evergreen URL the
site itself labels "never delete") lists every property as an <li> with
name, full street address, and the /apartments/<slug>/ detail link.
We fetch it once and keep only properties whose address is in a Santa
Clara County city.

Leasing status is NOT on this page (EAH uses a RealPage availability API
per property); records carry the inventory + contact-page link, which is
the breadth signal. Status enrichment is a possible future improvement.

Public entry point:
    scrape_eah(authority, url)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime as _dt
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from housing_list_search.access import polite_get

logger = logging.getLogger(__name__)

SEARCH_RESULTS_URL = "https://www.eahhousing.org/apartment-search-result-never-delete/"

SANTA_CLARA_CITIES = {
    "san jose",
    "san josé",
    "santa clara",
    "sunnyvale",
    "mountain view",
    "palo alto",
    "milpitas",
    "cupertino",
    "campbell",
    "gilroy",
    "morgan hill",
    "los gatos",
    "los altos",
    "saratoga",
    "stanford",
}


def parse_search_results(html_text: str, now_iso: str, source_url: str) -> list[dict[str, Any]]:
    """Parse the EAH all-properties list, filtered to Santa Clara County."""
    soup = BeautifulSoup(html_text, "html.parser")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for li in soup.find_all("li"):
        h = li.find("h2")
        if not h:
            continue
        link = h.find("a", href=re.compile(r"/apartments/[^/]+/?$"))
        if not link:
            continue
        name = link.get_text(strip=True)
        href = link.get("href", "")
        if not name or href in seen:
            continue

        addr_p = li.find("p")
        address = addr_p.get_text(" ", strip=True) if addr_p else ""

        city_match = re.search(r",\s*([A-Za-zÀ-ÿ' .]+),\s*(?:California|CA)\b", address)
        city = city_match.group(1).strip() if city_match else ""
        if city.lower() not in SANTA_CLARA_CITIES:
            continue

        # Absolute URL for stable listing_identity (#1084)
        detail_url = urljoin(source_url, href) if href else ""
        seen.add(detail_url or href)
        records.append(
            {
                "authority": "EAH Housing (Santa Clara County portfolio)",
                "property_name": name,
                "address": re.sub(r"\s*,\s*California\s*", ", CA ", address).strip(),
                "url": detail_url,
                "status": "Check with property",
                "administrator": "EAH Housing",
                "administrator_url": "https://www.eahhousing.org/",
                "notes": "EAH Housing managed property",
                "confidence": "high",
                "last_seen": now_iso,
                "first_seen": now_iso,
                "source": "eah:search_results",
                "source_url": source_url,
                "expires_at": "",
            }
        )

    return records


def scrape_eah(authority: str = "", url: str = "") -> list[dict[str, Any]]:
    """Public entry point. Single request to the all-properties list."""
    print("🧩 Running EAH Housing adapter (all-properties list, county filter)")
    now_iso = _dt.now().isoformat()
    target = url or SEARCH_RESULTS_URL

    from housing_list_search.access import require_response

    resp = require_response(polite_get(target), target, context="eah")

    records = parse_search_results(resp.text, now_iso, target)
    # Empty parse after successful fetch is soft-success that ages out inventory (#238).
    if not records:
        from housing_list_search.access import SourceFetchError

        raise SourceFetchError(
            "eah: all-properties list returned zero Santa Clara County properties "
            "(selector drift or empty response) — mark SCRAPE_FAILED"
        )
    print(f"   → EAH Housing: {len(records)} Santa Clara County properties")
    return records
