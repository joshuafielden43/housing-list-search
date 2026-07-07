"""
Extraction-layer integration tests (live network).

Run explicitly: pytest tests/test_extraction.py -m integration
CI runs unit tests only: pytest tests/ -m "not integration"
"""

import pytest

from housing_list_search.extraction import HousingRecord, extract_target


@pytest.mark.integration
def test_san_jose_dispatcher_returns_real_records():
    """San José portal (Bloom) via extract_target dispatch path."""
    records = extract_target("https://housing.sanjoseca.gov/listings", "City of San José")
    assert len(records) > 5, "Expected many real San José listings"
    r = records[0]
    # extract_target (via dispatch) returns plain dicts for uniformity
    assert isinstance(r, dict)
    assert r.get("authority") == "City of San José"
    assert r.get("property_name"), "Property name must be present"
    doc = r.get("document_url") or r.get("url") or ""
    assert doc.startswith("https://housing.sanjoseca.gov/listing/"), (
        "Should have direct listing link"
    )


@pytest.mark.integration
def test_gilroy_pdf_dispatcher_returns_real_records():
    """Gilroy DocumentCenter PDF via extract_target dispatch path."""
    records = extract_target(
        "https://www.cityofgilroy.org/DocumentCenter/View/16518", "City of Gilroy"
    )
    assert len(records) > 5, "Expected multiple rows from the PDF table"
    r = records[0]
    # extract_target (via dispatch) returns plain dicts for uniformity
    assert isinstance(r, dict)
    auth = r.get("authority") or ""
    assert "Gilroy" in auth or not auth
    assert r.get("property_name"), "Property name must be extracted from table"
    assert r.get("address"), "Address must be extracted from table"


def test_housing_record_to_dict_roundtrip():
    """Basic sanity on the dataclass used by the whole pipeline."""
    rec = HousingRecord(
        authority="Test",
        property_name="Example Gardens",
        address="123 Main St",
        phone="(555) 123-4567",
        email="test@example.org",
        confidence="high",
    )
    d = rec.to_dict()
    assert d["property_name"] == "Example Gardens"
    assert d["address"] == "123 Main St"
    assert d["url"] == ""  # because we didn't set document_url


def test_bloom_ssr_extraction_robust(monkeypatch):
    """Unit regression for Bloom SSR path (tolerant parsing)."""
    from housing_list_search.extraction.bloom_housing import _fetch_via_ssr

    sample_html = (
        '<script id="__NEXT_DATA__" type="application/json">'
        '{"props":{"pageProps":{"openListings":[{"id":1,"title":"Test Gardens","address":"123 Main St, San Jose","status":"open"}],"closedListings":[]}}}'
        "</script>"
    )

    class FakeResp:
        status_code = 200
        text = sample_html
        url = "https://housing.sanjoseca.gov/listings"

    def fake_polite_get(url):
        return FakeResp()

    monkeypatch.setattr("housing_list_search.extraction.bloom_housing.polite_get", fake_polite_get)

    open_l, closed_l = _fetch_via_ssr("https://housing.sanjoseca.gov/listings")
    assert len(open_l) == 1
    assert open_l[0].get("title") == "Test Gardens"
    assert len(closed_l) == 0
