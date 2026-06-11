"""
Fixture-based adapter smoke tests (no network, no Playwright).

Locks parsing logic for John Stewart, GIS, and CivicPlus helpers so regressions
are caught without live portal access.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

bs4 = pytest.importorskip("bs4", reason="bs4 not installed — skipping adapter fixture tests")
BeautifulSoup = bs4.BeautifulSoup


# ---------------------------------------------------------------------------
# john_stewart.py — SCCHA property-box parser
# ---------------------------------------------------------------------------

SCCHA_DIRECTORY_HTML = """
<html><body>
<div class="property-box">
  <h3>Oak Creek Apartments</h3>
  100 Oak St, San Jose, CA 95112
  Senior Section 8
  <a href="https://jscosccha.com/property/oak">Learn More</a>
</div>
<div class="property-box">
  <h3>De Rose Manor</h3>
  200 Bascom Ave, San Jose, CA 95128
  Family
</div>
</body></html>
"""


class TestJohnStewartAdapter:
    def test_sccha_directory_parses_property_boxes(self):
        mock_resp = MagicMock()
        mock_resp.text = SCCHA_DIRECTORY_HTML

        with patch("housing_list_search.adapters.john_stewart.polite_get", return_value=mock_resp):
            from housing_list_search.adapters.john_stewart import scrape_john_stewart
            records = scrape_john_stewart(
                "https://www.scchousingauthority.org/section-8/for-participants/"
                "for-new-applicants/properties-list/"
            )

        names = {r["property_name"] for r in records}
        assert "Oak Creek Apartments" in names
        assert "De Rose Manor" in names
        assert any("95112" in (r.get("address") or "") for r in records)


# ---------------------------------------------------------------------------
# gis_extraction.py — embedded GeoJSON in .js
# ---------------------------------------------------------------------------

CUpertino_UNITS_JS = """
var rentals = {
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {"Name": "Arioso", "NumUnits": 20}
    },
    {
      "type": "Feature",
      "properties": {"Name": "The Veranda", "NumUnits": 19}
    }
  ]
};
"""


class TestGisExtractionAdapter:
    def test_embedded_geojson_js_parses_features(self):
        mock_resp = MagicMock()
        mock_resp.text = CUpertino_UNITS_JS

        with patch("housing_list_search.adapters.gis_extraction.polite_get", return_value=mock_resp):
            from housing_list_search.adapters.gis_extraction import extract_gis_portfolio
            records = extract_gis_portfolio(
                "https://gis.cupertino.org/bmr_units/units.js",
                "City of Cupertino BMR (Rental)",
                administrator="Rise Housing",
            )

        names = {r["property_name"] for r in records}
        assert "Arioso" in names
        assert "The Veranda" in names
        assert all(r.get("administrator") == "Rise Housing" for r in records)


# ---------------------------------------------------------------------------
# civicplus.py — Froala availability list parser
# ---------------------------------------------------------------------------

FROALA_AVAILABILITY_HTML = """
<div class="fr-view">
  <ul role="presentation">
    <li><strong>Wheeler Manor - 3 available units</strong>
      Contact: (408) 555-1212, leasing@example.org</li>
  </ul>
  <ul role="presentation">
    <li><strong>Cannery Apartments - 5 units available</strong></li>
  </ul>
</div>
"""


class TestCdnAdapter:
    def test_froala_availability_blocks_parser(self):
        from housing_list_search.adapters.civicplus import _parse_froala_availability_blocks

        container = BeautifulSoup(FROALA_AVAILABILITY_HTML, "html.parser")
        records = _parse_froala_availability_blocks(container, "City of Gilroy")

        names = {r["property_name"] for r in records}
        assert "Wheeler Manor" in names
        assert "Cannery Apartments" in names
        wheeler = next(r for r in records if r["property_name"] == "Wheeler Manor")
        assert wheeler["available_units"] == "3"
        assert wheeler["phone"] == "(408) 555-1212"
        assert wheeler["email"] == "leasing@example.org"