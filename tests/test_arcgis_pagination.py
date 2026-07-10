"""#1079: ArcGIS REST pagination — page until complete or fail-loud."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from housing_list_search.access import SourceFetchError
from housing_list_search.adapters import gis_extraction as gis


def _feature(i: int) -> dict:
    return {
        "attributes": {
            "Name": f"Property {i}",
            "Address": f"{i} Main St",
            "Agency": "Test Manager",
        }
    }


class FakeResp:
    def __init__(self, payload: dict):
        self._payload = payload

    def json(self):
        return self._payload


def test_arcgis_single_page_complete():
    payload = {
        "features": [_feature(1), _feature(2)],
        "exceededTransferLimit": False,
    }
    with patch.object(gis, "polite_get", return_value=FakeResp(payload)) as mock_get:
        records = gis._parse_arcgis_rest(
            "https://gis.example.gov/arcgis/rest/services/X/MapServer/0/query",
            "City of Test",
        )
    assert len(records) == 2
    assert mock_get.call_count == 1
    assert "resultOffset=0" in mock_get.call_args[0][0]
    assert records[0]["property_name"] == "Property 1"


def test_arcgis_pages_until_not_exceeded():
    page1 = {
        "features": [_feature(i) for i in range(gis._ARCGIS_PAGE_SIZE)],
        "exceededTransferLimit": True,
    }
    page2 = {
        "features": [_feature(gis._ARCGIS_PAGE_SIZE), _feature(gis._ARCGIS_PAGE_SIZE + 1)],
        "exceededTransferLimit": False,
    }
    calls: list[str] = []

    def fake_get(url, **_k):
        calls.append(url)
        if "resultOffset=0" in url:
            return FakeResp(page1)
        return FakeResp(page2)

    with patch.object(gis, "polite_get", side_effect=fake_get):
        records = gis._parse_arcgis_rest(
            "https://gis.example.gov/arcgis/rest/services/X/MapServer/0",
            "City of Test",
        )
    assert len(records) == gis._ARCGIS_PAGE_SIZE + 2
    assert len(calls) == 2
    assert f"resultOffset={gis._ARCGIS_PAGE_SIZE}" in calls[1]


def test_arcgis_pagination_cap_raises():
    full_page = {
        "features": [_feature(i) for i in range(gis._ARCGIS_PAGE_SIZE)],
        "exceededTransferLimit": True,
    }

    with patch.object(gis, "polite_get", return_value=FakeResp(full_page)):
        with pytest.raises(SourceFetchError, match="pagination|max_pages") as ei:
            gis._parse_arcgis_rest(
                "https://gis.example.gov/arcgis/rest/services/X/MapServer/0/query",
                "City of Test",
            )
    assert len(ei.value.partial) == gis._ARCGIS_PAGE_SIZE * gis._ARCGIS_MAX_PAGES
