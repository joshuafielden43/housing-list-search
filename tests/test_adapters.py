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

# ---------------------------------------------------------------------------
# Property-manager portfolio adapters (alta, charities_housing, midpen,
# eden, eah) — offline fixtures mirroring each vendor's live markup
# ---------------------------------------------------------------------------

ALTA_DIRECTORY_HTML = """
<div class="prop-box">
  <div class="prop-image rel">
    <a class="image-hover" href="https://altahousing.org/properties/alma-place/"><img alt="Alma Place"/></a>
    <div class="box-header prop-flag">Waitlist Closed</div>
  </div>
  <div class="prop-content">
    <div class="block-title prop-title"><a href="https://altahousing.org/properties/alma-place/">Alma Place</a></div>
    <div class="block-desc serif italic">753 Alma Street, Palo Alto, CA 94301</div>
  </div>
</div>
<div class="prop-box">
  <div class="prop-image rel">
    <div class="box-header prop-flag">Waitlist Open</div>
  </div>
  <div class="prop-content">
    <div class="block-title prop-title"><a href="https://altahousing.org/properties/eagle-park/">Eagle Park</a></div>
    <div class="block-desc serif italic">1701 W El Camino Real, Mountain View, CA 94040</div>
  </div>
</div>
"""


class TestAltaPropertyDirectory:
    def test_directory_cards_parsed(self, monkeypatch):
        from housing_list_search.adapters import alta

        class FakeResp:
            text = ALTA_DIRECTORY_HTML

        monkeypatch.setattr(alta, "polite_get", lambda url: FakeResp())
        records = alta.scrape_property_directory("City of Palo Alto")

        assert len(records) == 2
        alma = next(r for r in records if r["property_name"] == "Alma Place")
        assert alma["listing_status"] == "closed"
        assert "Palo Alto" in alma["address"]
        eagle = next(r for r in records if r["property_name"] == "Eagle Park")
        assert eagle["listing_status"] == "waitlist"
        assert eagle["url"].endswith("/eagle-park/")


CHARITIES_FIND_A_HOME_HTML = """
<div class="h_apart_ctc">
  <div class="heading_h4"><h4><a href="https://charitieshousing.org/property/archer-studios/">Archer Studios</a></h4></div>
  <div class="apart_address_ct">
    <div class="apart_address_item"><p><a href="javascript:;">98 Archer St - San Jose<br/>CA 95112, USA</a></p></div>
    <div class="apart_address_item"><p><a href="mailto:archer@charitieshousing.org">archer@charitieshousing.org</a></p></div>
    <div class="apart_address_item"><p><a href="tel:(408) 217-8562">(408) 217-8562</a></p></div>
  </div>
  <div class="apart_unit_type"><div class="unit_type_out"><div class="unit_type_head"><h5>Unit Type:</h5><p>Studio</p></div></div></div>
</div>
"""


class TestCharitiesHousingAdapter:
    def test_find_a_home_cards_parsed(self):
        from housing_list_search.adapters.charities_housing import _parse_find_a_home

        records = _parse_find_a_home(CHARITIES_FIND_A_HOME_HTML, "2026-06-10T00:00:00")
        assert len(records) == 1
        r = records[0]
        assert r["property_name"] == "Archer Studios"
        assert r["email"] == "archer@charitieshousing.org"
        assert r["phone"] == "(408) 217-8562"
        assert r["listing_status"] == "open"
        assert "San Jose" in r["address"] and not r["address"].endswith("USA")
        assert r["unit_types"] == "Studio"


MIDPEN_CARD_HTML = """
<div class="elementor elementor-1027 elementor-location-single post-3008">
  <a href="https://www.midpen-housing.org/property/arbor-park/"><img alt=""/></a>
  <span>Wait List Open</span> <span>Family</span>
  <h3><a href="https://www.midpen-housing.org/property/arbor-park/">Arbor Park</a></h3>
  <p>Arbor Park is an affordable housing community.</p>
  <p>San Jose, CA</p>
</div>
"""


class TestMidPenAdapter:
    def test_card_parsed(self):
        from housing_list_search.adapters.midpen import _parse_card

        card = BeautifulSoup(MIDPEN_CARD_HTML, "html.parser").find("div")
        rec = _parse_card(card, "2026-06-10T00:00:00", "test-url")
        assert rec["property_name"] == "Arbor Park"
        assert rec["status"] == "Wait List Open"
        assert rec["listing_status"] == "waitlist"
        assert rec["address"] == "San Jose, CA"
        assert "family" in rec["eligibility_flags"]

    def test_page_wrapper_with_many_properties_skipped(self):
        from housing_list_search.adapters.midpen import _parse_card

        wrapper_html = """
        <div class="elementor-location-single">
          <a href="https://www.midpen-housing.org/property/a/">A</a>
          <a href="https://www.midpen-housing.org/property/b/">B</a>
        </div>"""
        card = BeautifulSoup(wrapper_html, "html.parser").find("div")
        assert _parse_card(card, "now", "u") is None


EDEN_GRID_HTML = """
<div class="property-grid listing-grid"><ul>
  <li>
    <a href="https://edenhousing.org/properties/801-alma/">Accepting Applications</a>
    <div><h3><a href="https://edenhousing.org/properties/801-alma/">801 Alma</a></h3>
    <p>Palo Alto, California</p><span>50</span></div>
  </li>
  <li>
    <a href="https://edenhousing.org/properties/cambrian-center/">Closed</a>
    <div><h3><a href="https://edenhousing.org/properties/cambrian-center/">Cambrian Center</a></h3>
    <p>San Jose, California</p><span>153</span></div>
  </li>
</ul></div>
"""


class TestEdenAdapter:
    def test_grid_parsed_with_status_from_badge_anchor(self):
        from housing_list_search.adapters.eden import parse_property_grid

        records = parse_property_grid(EDEN_GRID_HTML, "2026-06-10T00:00:00", "t")
        assert len(records) == 2
        alma = next(r for r in records if r["property_name"] == "801 Alma")
        assert alma["status"] == "Accepting Applications"
        assert alma["listing_status"] == "open"
        assert alma["address"] == "Palo Alto, CA"
        cambrian = next(r for r in records if r["property_name"] == "Cambrian Center")
        assert cambrian["listing_status"] == "closed"


EAH_LIST_HTML = """
<ul>
  <li><div class="inner_img_cont">
    <h2><a href="https://www.eahhousing.org/apartments/art-ark-apartments/">Art Ark Apartments</a></h2>
    <p><a href="https://www.eahhousing.org/apartments/art-ark-apartments/">1058 South Fifth Street, San Jose, California 95112</a></p>
  </div></li>
  <li><div class="inner_img_cont">
    <h2><a href="https://www.eahhousing.org/apartments/agave/">Agave</a></h2>
    <p><a href="https://www.eahhousing.org/apartments/agave/">2052 Lake Avenue, Altadena, California 91001</a></p>
  </div></li>
</ul>
"""


class TestEahAdapter:
    def test_county_filter_keeps_only_santa_clara(self):
        from housing_list_search.adapters.eah import parse_search_results

        records = parse_search_results(EAH_LIST_HTML, "2026-06-10T00:00:00", "t")
        assert len(records) == 1
        assert records[0]["property_name"] == "Art Ark Apartments"
        assert "San Jose" in records[0]["address"]


FIRST_HOUSING_CARD_HTML = """
<div><div><div>
  <h4>945 Lundy Avenue, San Jose, CA 95133</h4>
  <p>Office: 408-254-4540</p>
  <p>Email: bettyann@jsco.net</p>
  <a href="https://www.firsthousing.org/betty-ann-gardens">Information</a>
</div></div></div>
"""


class TestFirstHousingAdapter:
    def test_card_parsed_with_slug_name(self):
        from housing_list_search.adapters.first_housing import parse_portfolio

        records = parse_portfolio(FIRST_HOUSING_CARD_HTML, "2026-06-10T00:00:00", "t")
        assert len(records) == 1
        r = records[0]
        assert r["property_name"] == "Betty Ann Gardens"
        assert r["address"] == "945 Lundy Avenue, San Jose, CA 95133"
        assert r["phone"] == "408-254-4540"
        assert r["email"] == "bettyann@jsco.net"
