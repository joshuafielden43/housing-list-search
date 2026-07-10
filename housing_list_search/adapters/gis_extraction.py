"""
GIS Extraction Adapter (Municipal / One-Off Portfolio Pattern)

This adapter handles the common "city as coordinator" model where a municipality
publishes its affordable / BMR portfolio primarily through a GIS layer, often
combined with annual waitlist PDFs. Individual properties are frequently
managed by multiple independent nonprofit housing providers ("federated managers").

This is deliberately different from centralized vendor platforms (e.g. John Stewart).

CURRENT DESIGN ASSUMPTION (May 2026)
------------------------------------
We treat the municipality's published GIS data as the authoritative source for
the portfolio (property names, unit counts, locations) until real-world
experience shows systematic issues. Individual manager sites are considered
secondary sources for operational details (current leasing contact, exact
application process, real-time status) and are not required for a working
first pass.

Reference data (Cupertino, May 2026):
- 11 rental BMR properties published via GIS (Arioso 20, Aviare 22, Biltmore 2,
  Westport Senior 48, The Veranda 19, Forge Homestead 15, Greenwood Court 4,
  The Hamptons 34, The Markham 17, Park Center 4, Vista Village 24).
- Waitlists published as annual anonymous lottery PDFs (not property-level data).
- Actual per-property management distributed across multiple nonprofits.

PATTERN THIS ADAPTER REPRESENTS
-------------------------------
Many smaller or mid-sized municipalities do not run a full modern application
portal. Instead they:
- Maintain a GIS map of their deed-restricted / BMR units.
- Publish annual (often anonymous lottery) waitlists as PDFs.
- Point to multiple different property managers for day-to-day operations.

This adapter is the reference implementation for that pattern. Future one-off
adapters for similar situations should follow the same structure and
documentation standards.

HOW TO USE THIS FILE AS A TEMPLATE FOR NEW ONE-OFFS
---------------------------------------------------
1. Copy this file and rename it after the dominant data source or tool
   (e.g. `municipal_gis.py`, `pdf_waitlist.py`, `arcgis_portfolio.py`).

2. Update the module docstring with the specific city's situation and any
   new assumptions or workflow variants discovered.

3. Implement or extend the parser functions for the concrete data formats
   you encounter (embedded GeoJSON, ArcGIS FeatureServer, custom PDF layouts,
   etc.).

4. Keep the public entry point (`extract_gis_portfolio`) as the stable
   interface that the rest of the system calls.

5. Document clearly in the docstring:
   - What the city actually publishes vs what lives elsewhere.
   - The workflow the city expects applicants to follow.
   - Any known limitations or future improvement areas.

This discipline is how we keep results deterministic even when different
people (or different LLMs) create the next adapter.

Current reference implementation: City of Cupertino, California (Santa Clara County).

=============================================================================
SCOPE & GUARDRAILS
=============================================================================

This section defines the intended scope of the adapter and the principles
that should guide future extensions. The goal is to keep the adapter
maintainable and to allow the pattern to improve over time as more
municipalities are encountered.

In Scope
- Extraction of portfolio data published by a municipality through GIS
  layers (property names, unit counts, and locations when available).
- Extraction of operational details that individual property managers
  publish on their own public websites (phone numbers, emails, addresses,
  application instructions, status language, and links to documents).
- Support for common municipal publication methods, including embedded
  GeoJSON, direct GeoJSON endpoints, and ArcGIS REST services.

Out of Scope
- Anonymous applicant waitlists that contain only lottery numbers,
  preference points, and position rankings. These do not identify
  individual properties and provide limited value for opportunity
  matching.
- Contacting or locating individual public servants or city staff.
  The city (or its designated program administrator) is assumed to
  manage the official list.
- Discovery of contact information or details that are not publicly
  published on the property or manager website.

Known Low-Value Patterns
- Annual anonymous lottery waitlist PDFs (common in some city BMR
  programs). These typically list only applicant identifiers and
  rankings. They should be noted but generally skipped for structured
  extraction unless they contain property-level information.
- Overly broad keyword scraping on vendor sites when more structured
  data is available on the same platform.

Extension Guidance
- When a new municipality presents a similar GIS-driven or city-coordinated
  model, extend this adapter or create a focused variant within the same
  file.
- When a meaningfully different publication pattern is discovered, create
  a new adapter and document the new pattern so the overall skill set
  improves over time.
- All new work should preserve the naming convention (adapter named after
  the data source or tool) and the documentation standards established here.

These guardrails exist so that future extensions remain consistent and
the adapter can evolve without requiring a full rewrite.
=============================================================================
"""

from __future__ import annotations

import json
import logging
from datetime import datetime as _dt
from typing import Any
from urllib.parse import urljoin

from bs4 import BeautifulSoup

from housing_list_search.access import polite_get

logger = logging.getLogger(__name__)


# =============================================================================
# PUBLIC API
# =============================================================================


def extract_gis_portfolio(
    source: str,
    authority: str = "",
    administrator: str = "",
    administrator_url: str = "",
    administrator_phone: str = "",
    administrator_contact: str = "",
) -> list[dict[str, Any]]:
    """
    Main entry point for municipal/GIS-based portfolio extraction.

    `source` can currently be:
    - A direct URL to a GeoJSON file or FeatureCollection.
    - A URL to a page that embeds GeoJSON in a JavaScript variable (Cupertino style).
    - A URL to an ArcGIS FeatureServer / MapServer query endpoint (future).

    Returns a list of normalized property records suitable for the rest of
    the pipeline (name, address if available, unit count, source URL, etc.).

    If administrator information is provided via the target configuration
    (for cases where the city contracts a third party like Rise Housing to
    manage the waitlist/portfolio), it will be attached to the records.
    This allows generic contact info (URL, phone, email) to travel with the
    extracted data. The information can come from scraping, LLM-assisted
    discovery, or direct human entry in TARGETS.md.

    The caller is responsible for deciding whether to further enrich records
    by visiting individual property manager sites.
    """
    lower = source.lower()

    if lower.endswith(".js") or "units.js" in lower or "purchase.js" in lower:
        return _parse_embedded_geojson_js(
            source,
            authority,
            administrator=administrator,
            administrator_url=administrator_url,
            administrator_phone=administrator_phone,
            administrator_contact=administrator_contact,
        )

    if "geojson" in lower or lower.endswith(".json"):
        return _parse_direct_geojson(source, authority)

    if "featureserver" in lower or "mapserver" in lower or "arcgis" in lower:
        return _parse_arcgis_rest(source, authority)

    # Fallback: try to treat it as a page that might contain embedded data
    return _parse_page_for_embedded_gis(
        source,
        authority,
        administrator=administrator,
        administrator_url=administrator_url,
        administrator_phone=administrator_phone,
        administrator_contact=administrator_contact,
    )


# =============================================================================
# PARSERS
# =============================================================================


def _parse_embedded_geojson_js(
    url: str,
    authority: str,
    administrator: str = "",
    administrator_url: str = "",
    administrator_phone: str = "",
    administrator_contact: str = "",
) -> list[dict[str, Any]]:
    """
    Handles cases like Cupertino where the city serves GeoJSON inside a .js file
    as a JavaScript variable (e.g. var rentals = { "type": "FeatureCollection", ... }).
    """
    logger.debug(f"GIS (embedded JS) on {url}")

    from housing_list_search.access import require_response

    resp = require_response(polite_get(url), url, context="gis/embedded_js")

    text = resp.text

    # Find the first '{' that starts the FeatureCollection
    start = text.find("{")
    if start == -1:
        logger.debug("No JSON object found in JS file")
        return []

    # Find the matching closing brace for the top-level object
    # Simple heuristic: take everything from first { to last }
    end = text.rfind("}") + 1
    json_str = text[start:end]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        logger.debug(f"Failed to parse JSON from {url}: {e}")
        return []

    return _features_to_records(
        data,
        url,
        authority,
        administrator=administrator,
        administrator_url=administrator_url,
        administrator_phone=administrator_phone,
        administrator_contact=administrator_contact,
    )


def _parse_direct_geojson(url: str, authority: str) -> list[dict[str, Any]]:
    """Handles direct .geojson or JSON FeatureCollection endpoints."""
    logger.debug(f"GIS (direct GeoJSON) on {url}")

    from housing_list_search.access import require_response

    resp = require_response(polite_get(url), url, context="gis/geojson")

    try:
        data = resp.json()
    except Exception as e:
        logger.debug(f"Failed to parse JSON: {e}")
        return []

    return _features_to_records(data, url, authority)


def _parse_arcgis_rest(url: str, authority: str) -> list[dict[str, Any]]:
    """
    ArcGIS FeatureServer / MapServer query endpoints.

    Reference implementation: City of Sunnyvale (June 2026) —
    gis.sunnyvale.ca.gov/arcgis/rest/services/CDD/AffordableHousing/MapServer/0/query
    The GIS subdomain is publicly queryable even though the main city site is
    Akamai WAF-blocked. The layer carries Name, Address, Agency (the property
    manager), Income_Level, AffordableUnits, Bedrooms, PopulationServed,
    PhoneNumber, Website, and last-edited audit fields.

    `url` may be the layer URL or a full /query URL; query params are
    normalized either way.
    """
    logger.debug(f"GIS (ArcGIS) on {url}")

    query_url = url.split("?")[0]
    if not query_url.rstrip("/").endswith("/query"):
        query_url = query_url.rstrip("/") + "/query"

    from housing_list_search.access import SourceFetchError, require_response

    full = f"{query_url}?where=1%3D1&outFields=*&f=json"
    resp = require_response(polite_get(full), full, context="gis/arcgis")

    try:
        data = resp.json()
    except Exception as exc:
        raise SourceFetchError(f"gis/arcgis: non-JSON from {url}: {exc}") from exc

    if "error" in data:
        raise SourceFetchError(f"gis/arcgis: layer error from {url}: {data['error']}")

    return _arcgis_features_to_records(data, url, authority)


def _arcgis_features_to_records(
    data: dict[str, Any], source_url: str, authority: str
) -> list[dict[str, Any]]:
    """Convert ArcGIS REST query results (features[].attributes) to records."""
    features = data.get("features", [])
    if not isinstance(features, list):
        return []

    def pick(attrs: dict[str, Any], *names: str) -> str:
        lower = {k.lower(): v for k, v in attrs.items()}
        for n in names:
            v = lower.get(n.lower())
            if v not in (None, "", "Null"):
                return str(v).strip()
        return ""

    records: list[dict[str, Any]] = []
    now_iso = _dt.now().isoformat()

    for feat in features:
        attrs = feat.get("attributes", {}) if isinstance(feat, dict) else {}
        if not attrs:
            continue

        name = pick(attrs, "Name", "PROPERTY_NAME", "ProjectName", "SiteName")
        if not name:
            continue

        units = pick(attrs, "AffordableUnits", "NumUnits", "TotalUnits", "Units", "UNIT_COUNT")
        manager = pick(attrs, "Agency", "Manager", "PropertyManager", "Owner")
        notes_bits = ["Source: municipal ArcGIS layer"]
        for label, *fields in (
            ("Building type", "BuildingType", "ProjectType"),
            ("Income level", "Income_Level", "IncomeLevel", "AMI"),
            ("Population", "PopulationServed", "Population"),
        ):
            val = pick(attrs, *fields)
            if val:
                notes_bits.append(f"{label}: {val}")

        rec: dict[str, Any] = {
            "authority": authority or "Municipal GIS Portfolio",
            "property_name": name,
            "address": pick(attrs, "Address", "SiteAddress", "FullAddress"),
            "phone": pick(attrs, "PhoneNumber", "Phone"),
            "url": pick(attrs, "Website", "URL", "Link"),
            "unit_count": units,
            "bedrooms": pick(attrs, "Bedrooms", "UnitTypes"),
            "notes": " | ".join(notes_bits),
            "confidence": "high",  # structured municipal data, not scraped HTML
            "last_seen": now_iso,
            "first_seen": now_iso,
            "source": f"gis:{authority.lower().replace(' ', '_')}",
            "source_url": source_url,
            "expires_at": "",
        }
        if manager:
            rec["administrator"] = manager
            rec["notes"] += f" | Manager: {manager}"

        records.append(rec)

    print(f"   → Extracted {len(records)} properties from ArcGIS source")
    return records


def _parse_page_for_embedded_gis(
    url: str,
    authority: str,
    administrator: str = "",
    administrator_url: str = "",
    administrator_phone: str = "",
    administrator_contact: str = "",
) -> list[dict[str, Any]]:
    """
    Enhanced page scanner for municipal BMR/GIS pages.

    Strategy:
    - Look for inline <script> tags containing GeoJSON FeatureCollection.
    - Look for linked .js files that are likely to contain GIS data
      (filenames/paths containing units, bmr, rentals, portfolio, gis, etc.).
    - Attempt to fetch and parse promising candidates.

    This allows users to put the human-friendly BMR overview URL in TARGETS.md
    while the adapter still finds the actual data.
    """
    logger.debug(f"GIS (page scan) on {url}")

    from housing_list_search.access import require_response

    resp = require_response(polite_get(url), url, context="gis/page_scan")

    soup = BeautifulSoup(resp.text, "html.parser")
    base_url = url

    candidates = []

    # 1. Inline scripts with FeatureCollection
    for script in soup.find_all("script"):
        if script.string and "FeatureCollection" in script.string:
            try:
                start = script.string.find("{")
                end = script.string.rfind("}") + 1
                data = json.loads(script.string[start:end])
                recs = _features_to_records(
                    data,
                    url,
                    authority,
                    administrator=administrator,
                    administrator_url=administrator_url,
                    administrator_phone=administrator_phone,
                    administrator_contact=administrator_contact,
                )
                if recs:
                    return recs
            except Exception:
                continue

    # 2. External script / link candidates that look like GIS data
    for tag in soup.find_all(["script", "a", "link"]):
        src = tag.get("src") or tag.get("href")
        if not src:
            continue
        src_lower = src.lower()
        if any(
            kw in src_lower
            for kw in ["units", "bmr", "rentals", "portfolio", "gis", "features", "geojson"]
        ):
            full_url = urljoin(base_url, src)
            candidates.append(full_url)

    # Deduplicate while preserving order
    seen = set()
    candidates = [c for c in candidates if not (c in seen or seen.add(c))]

    for candidate in candidates:
        try:
            recs = _parse_embedded_geojson_js(
                candidate,
                authority,
                administrator=administrator,
                administrator_url=administrator_url,
                administrator_phone=administrator_phone,
                administrator_contact=administrator_contact,
            )
            if recs:
                print(f"   Discovered GIS data at: {candidate}")
                return recs
        except Exception:
            continue

    # 3. Known municipal GIS discovery patterns
    # Cupertino (gis.cupertino.org is a separate subdomain)
    if "cupertino" in url.lower() and "bmr" in url.lower():
        candidates = [
            "https://gis.cupertino.org/bmr_units/units.js",
            "https://gis.cupertino.org/bmr_units",
        ]
        for cand in candidates:
            try:
                recs = _parse_embedded_geojson_js(
                    cand,
                    authority,
                    administrator=administrator,
                    administrator_url=administrator_url,
                    administrator_phone=administrator_phone,
                    administrator_contact=administrator_contact,
                )
                if recs:
                    print(f"   Discovered Cupertino GIS data at: {cand}")
                    return recs
            except Exception:
                continue

    logger.debug("No usable GIS data discovered")
    return []


# =============================================================================
# HELPERS
# =============================================================================


def _features_to_records(
    geojson: dict[str, Any],
    source_url: str,
    authority: str,
    administrator: str = "",
    administrator_url: str = "",
    administrator_phone: str = "",
    administrator_contact: str = "",
) -> list[dict[str, Any]]:
    """Convert a GeoJSON FeatureCollection into normalized property records."""
    if not isinstance(geojson, dict):
        return []

    features = geojson.get("features", [])
    if not isinstance(features, list):
        return []

    records: list[dict[str, Any]] = []

    for feat in features:
        props = feat.get("properties", {}) if isinstance(feat, dict) else {}

        name = (
            props.get("Name")
            or props.get("name")
            or props.get("PROPERTY_NAME")
            or props.get("ProjectName")
            or props.get("description")
            or "Property"
        )

        units = (
            props.get("NumUnits")
            or props.get("units")
            or props.get("UNIT_COUNT")
            or props.get("TotalUnits")
        )

        rec: dict[str, Any] = {
            "authority": authority or "Municipal GIS Portfolio",
            "property_name": str(name).strip(),
            "address": "",  # GIS layers often only have point geometry, not full address
            "unit_count": str(units) if units else "",
            "source": source_url,
            "notes": f"Source: municipal GIS layer ({source_url})",
            "confidence": "medium",
        }

        # Attach delegated administrator info when known (e.g. Rise Housing for Cupertino)
        if administrator:
            rec["administrator"] = administrator
            if administrator_url:
                rec["administrator_url"] = administrator_url
            if administrator_phone:
                rec["administrator_phone"] = administrator_phone
            if administrator_contact:
                rec["administrator_contact"] = administrator_contact
            rec["notes"] += f" | Administrator: {administrator}"

        # If we have geometry, we can store a rough location note
        geometry = feat.get("geometry", {}) if isinstance(feat, dict) else {}
        if geometry.get("type") == "Point":
            coords = geometry.get("coordinates", [])
            if len(coords) >= 2:
                rec["notes"] += f" | approx lat/lon: {coords[1]:.5f}, {coords[0]:.5f}"

        now_iso = _dt.now().isoformat()
        rec["last_seen"] = now_iso
        rec["first_seen"] = now_iso
        rec["source"] = f"gis:{authority.lower().replace(' ', '_')}"
        rec["source_url"] = source_url
        rec["expires_at"] = ""

        records.append(rec)

    print(f"   → Extracted {len(records)} properties from GIS source")
    return records


# =============================================================================
# QUICK USAGE / VALIDATION
# =============================================================================
# Run this file directly to test the Cupertino reference case:
#
#   python -m housing_list_search.adapters.gis_extraction
#
# It will print the current portfolio extracted from the live Cupertino GIS.
# =============================================================================

if __name__ == "__main__":
    print("=== GIS Extraction – Cupertino Reference Run ===\n")
    base = "https://gis.cupertino.org/bmr_units/"
    administrator = "Rise Housing"
    administrator_url = "https://www.risehousing.com/applicants-cupertino-bmr-rental"
    administrator_phone = "(415) 301-5448"
    administrator_contact = "cupertino@risehousing.com"

    records = extract_gis_portfolio(
        base + "units.js",
        "City of Cupertino BMR (Rental)",
        administrator=administrator,
        administrator_url=administrator_url,
        administrator_phone=administrator_phone,
        administrator_contact=administrator_contact,
    ) + extract_gis_portfolio(
        base + "purchase.js",
        "City of Cupertino BMR (Ownership)",
        administrator=administrator,
        administrator_url=administrator_url,
        administrator_phone=administrator_phone,
        administrator_contact=administrator_contact,
    )

    print(f"Total records returned: {len(records)}\n")
    for r in records:
        print(
            f"  {r['property_name']:30} | Units: {r.get('unit_count', '?'):>3} | {r.get('authority', '')}"
        )

    print("\nDone.")
