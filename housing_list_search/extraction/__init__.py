"""
Extraction package public API.

Provides a unified way to turn a target (URL or known portal) into
a list of structured HousingRecord objects.

This is the integration point for the high-quality per-platform extractors
(pdf tables, Bloom Housing Next.js/REST, future ones).
"""

from __future__ import annotations

from typing import List

from .pdf import HousingRecord, extract_records_from_pdf
from .bloom_housing import extract_bloom_housing_listings, extract_san_jose_listings

# Hostnames known to be Bloom Housing instances.
# Add new instances here; no other code changes required if the instance
# is SSR (will use __NEXT_DATA__ path automatically) or already in
# bloom_housing._API_INSTANCES (will use REST API path).
_KNOWN_BLOOM_DOMAINS = {
    "housing.sanjoseca.gov",       # San José — SSR instance
    "housingbayarea.mtc.ca.gov",   # MTC Doorway — CSR/API instance
}


def extract_target(url: str, authority: str = "") -> List[HousingRecord]:
    """
    Main dispatcher.

    Given a URL (and optional authority label from TARGETS.md),
    returns a list of high-quality HousingRecord objects.

    Currently knows:
    - Any Bloom Housing instance (SSR or CSR/API) → bloom_housing adapter
    - Any .pdf or Gilroy DocumentCenter/View links → table-aware PDF extractor
    - Future: more platform-specific extractors will be added here.
    """
    u = (url or "").lower()
    auth = (authority or "").lower()

    # Bloom Housing platform — covers San José and MTC Doorway and any future instances
    from urllib.parse import urlparse
    host = urlparse(url).netloc.lower()
    if host in _KNOWN_BLOOM_DOMAINS:
        # For MTC Doorway, pass a city_filter if the authority is a specific city.
        # Without a filter, all Bay Area listings come back (may be intentional for
        # county-wide searches, but TARGETS.md city rows should filter to that city).
        city_filter = ""
        if "housingbayarea.mtc.ca.gov" in u and authority:
            # Strip common prefixes and any parenthetical qualifiers to get the
            # bare city name that matches listingsBuildingAddress.city in Bloom.
            # e.g. "City of Santa Clara (rentals via MTC Doorway)" → "Santa Clara"
            import re as _re
            city_filter = authority
            city_filter = city_filter.replace("City of ", "").replace("Town of ", "")
            city_filter = _re.sub(r"\s*\(.*\)\s*$", "", city_filter)
            city_filter = city_filter.strip()
        return extract_bloom_housing_listings(url, authority=authority, city_filter=city_filter)

    # Direct PDF or Gilroy DocumentCenter links
    if u.endswith(".pdf") or "documentcenter/view" in u or "documentcenter" in u:
        auth_label = authority or "City of Gilroy"
        return extract_records_from_pdf(url, authority=auth_label)

    # No high-quality extractor known for this target yet.
    # (Generic scraping is deliberately not here — it was too noisy.)
    return []


__all__ = [
    "HousingRecord",
    "extract_target",
    "extract_records_from_pdf",
    "extract_bloom_housing_listings",
    "extract_san_jose_listings",   # backwards-compatible shim
]
