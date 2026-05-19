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
import re
from typing import List, Dict, Any, Optional
from urllib.parse import urljoin, urlparse

import requests
from bs4 import BeautifulSoup

from housing_list_search.scraper import polite_get


# =============================================================================
# PUBLIC API
# =============================================================================

def extract_gis_portfolio(source: str, authority: str = "") -> List[Dict[str, Any]]:
    """
    Main entry point for municipal/GIS-based portfolio extraction.

    `source` can currently be:
    - A direct URL to a GeoJSON file or FeatureCollection.
    - A URL to a page that embeds GeoJSON in a JavaScript variable (Cupertino style).
    - A URL to an ArcGIS FeatureServer / MapServer query endpoint (future).

    Returns a list of normalized property records suitable for the rest of
    the pipeline (name, address if available, unit count, source URL, etc.).

    The caller is responsible for deciding whether to further enrich records
    by visiting individual property manager sites.
    """
    lower = source.lower()

    if lower.endswith(".js") or "units.js" in lower or "purchase.js" in lower:
        return _parse_embedded_geojson_js(source, authority)

    if "geojson" in lower or lower.endswith(".json"):
        return _parse_direct_geojson(source, authority)

    if "featureserver" in lower or "mapserver" in lower or "arcgis" in lower:
        return _parse_arcgis_rest(source, authority)

    # Fallback: try to treat it as a page that might contain embedded data
    return _parse_page_for_embedded_gis(source, authority)


# =============================================================================
# CONVENIENCE HELPERS FOR KNOWN PATTERNS
# =============================================================================

def extract_cupertino_gis() -> List[Dict[str, Any]]:
    """
    Convenience wrapper specifically for the City of Cupertino's current
    GIS publication method (embedded GeoJSON in /bmr_units/units.js and
    /bmr_units/purchase.js).

    This is useful for development, testing, and as a living example
    inside the template documentation.
    """
    base = "https://gis.cupertino.org/bmr_units/"

    rental = extract_gis_portfolio(base + "units.js", "City of Cupertino BMR (Rental)")
    ownership = extract_gis_portfolio(base + "purchase.js", "City of Cupertino BMR (Ownership)")

    return rental + ownership


# =============================================================================
# PARSERS
# =============================================================================

def _parse_embedded_geojson_js(url: str, authority: str) -> List[Dict[str, Any]]:
    """
    Handles cases like Cupertino where the city serves GeoJSON inside a .js file
    as a JavaScript variable (e.g. var rentals = { "type": "FeatureCollection", ... }).
    """
    print(f"🧩 Running GIS Extraction adapter (embedded GeoJSON in JS) on {url}")

    resp = polite_get(url)
    if not resp:
        return []

    text = resp.text

    # Find the first '{' that starts the FeatureCollection
    start = text.find("{")
    if start == -1:
        print("   No JSON object found in JS file")
        return []

    # Find the matching closing brace for the top-level object
    # Simple heuristic: take everything from first { to last }
    end = text.rfind("}") + 1
    json_str = text[start:end]

    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        print(f"   Failed to parse JSON from {url}: {e}")
        return []

    return _features_to_records(data, url, authority)


def _parse_direct_geojson(url: str, authority: str) -> List[Dict[str, Any]]:
    """Handles direct .geojson or JSON FeatureCollection endpoints."""
    print(f"🧩 Running GIS Extraction adapter (direct GeoJSON) on {url}")

    resp = polite_get(url)
    if not resp:
        return []

    try:
        data = resp.json()
    except Exception as e:
        print(f"   Failed to parse JSON: {e}")
        return []

    return _features_to_records(data, url, authority)


def _parse_arcgis_rest(url: str, authority: str) -> List[Dict[str, Any]]:
    """
    Placeholder for real ArcGIS FeatureServer / MapServer queries.
    Will be implemented when we encounter a live example.
    """
    print(f"🧩 Running GIS Extraction adapter (ArcGIS REST) on {url}")
    print("   ArcGIS REST parser not yet implemented — returning empty list")
    return []


def _parse_page_for_embedded_gis(url: str, authority: str) -> List[Dict[str, Any]]:
    """
    Last-resort parser that fetches an HTML page and looks for embedded
    GeoJSON or JavaScript variables containing feature data.
    """
    print(f"🧩 Running GIS Extraction adapter (page scan) on {url}")

    resp = polite_get(url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, "html.parser")

    # Look for script tags that might contain GeoJSON
    for script in soup.find_all("script"):
        if script.string and "FeatureCollection" in script.string:
            try:
                # Very naive extraction — real implementation would be smarter
                start = script.string.find("{")
                end = script.string.rfind("}") + 1
                data = json.loads(script.string[start:end])
                return _features_to_records(data, url, authority)
            except Exception:
                continue

    print("   No embedded GeoJSON found via page scan")
    return []


# =============================================================================
# HELPERS
# =============================================================================

def _features_to_records(
    geojson: Dict[str, Any],
    source_url: str,
    authority: str,
) -> List[Dict[str, Any]]:
    """Convert a GeoJSON FeatureCollection into normalized property records."""
    if not isinstance(geojson, dict):
        return []

    features = geojson.get("features", [])
    if not isinstance(features, list):
        return []

    records: List[Dict[str, Any]] = []

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

        rec: Dict[str, Any] = {
            "authority": authority or "Municipal GIS Portfolio",
            "property_name": str(name).strip(),
            "address": "",  # GIS layers often only have point geometry, not full address
            "unit_count": str(units) if units else "",
            "source": source_url,
            "notes": f"Source: municipal GIS layer ({source_url})",
            "confidence": "medium",
        }

        # If we have geometry, we can store a rough location note
        geometry = feat.get("geometry", {}) if isinstance(feat, dict) else {}
        if geometry.get("type") == "Point":
            coords = geometry.get("coordinates", [])
            if len(coords) >= 2:
                rec["notes"] += f" | approx lat/lon: {coords[1]:.5f}, {coords[0]:.5f}"

        records.append(rec)

    print(f"   → Extracted {len(records)} properties from GIS source")
    return records


def _normalize(text: str) -> str:
    return " ".join(text.split()) if text else ""


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
    records = extract_cupertino_gis()

    print(f"Total records returned: {len(records)}\n")
    for r in records:
        print(f"  {r['property_name']:30} | Units: {r.get('unit_count', '?'):>3} | {r.get('authority', '')}")

    print("\nDone.")