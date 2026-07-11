"""#1076 / #1077 / #1078 / #1081: soft-success must not age out inventory as STALE."""

from __future__ import annotations

from unittest.mock import MagicMock, patch

import pytest

from housing_list_search.access import SourceFetchError


class TestBloomSoftSuccess:
    def test_all_paths_empty_raises_source_fetch_error(self):
        import housing_list_search.extraction.bloom_housing as bh

        with (
            patch.object(bh, "_fetch_via_ssr", return_value=([], [])),
            patch.object(bh, "_fetch_via_api", return_value=([], [], True)),
            patch.object(bh, "_fetch_via_playwright", return_value=([], [])),
        ):
            with pytest.raises(SourceFetchError, match="All extraction paths"):
                bh.extract_bloom_housing_listings(
                    "https://housing.sanjoseca.gov/listings",
                    authority="City of San José",
                )

    def test_city_filter_zero_is_not_hard_fail(self):
        """Path succeeded; filter emptied inventory → legitimate zero, not SCRAPE_FAILED."""
        import housing_list_search.extraction.bloom_housing as bh

        open_items = [
            {
                "id": "1",
                "name": "Only San Jose",
                "status": "active",
                "listingsBuildingAddress": {"city": "San Jose", "street": "1 Main"},
            }
        ]
        with (
            patch.object(bh, "_fetch_via_ssr", return_value=(open_items, [])),
            patch.object(bh, "_fetch_via_api", side_effect=AssertionError("no api")),
            patch.object(bh, "_fetch_via_playwright", side_effect=AssertionError("no pw")),
        ):
            records = bh.extract_bloom_housing_listings(
                "https://housing.sanjoseca.gov/listings",
                authority="City of Nowhere",
                city_filter="Nowhere",
            )
        assert records == []

    def test_max_results_truncation_raises(self):
        import housing_list_search.extraction.bloom_housing as bh

        items = [
            {
                "id": str(i),
                "name": f"Prop {i}",
                "status": "active",
                "listingsBuildingAddress": {"city": "San Jose", "street": f"{i} Main"},
            }
            for i in range(5)
        ]
        with patch.object(bh, "_fetch_via_ssr", return_value=(items, [])):
            with pytest.raises(SourceFetchError, match="max_results") as ei:
                bh.extract_bloom_housing_listings(
                    "https://housing.sanjoseca.gov/listings",
                    authority="City of San José",
                    max_results=2,
                )
            assert len(ei.value.partial) == 2


class TestPdfSoftSuccess:
    def test_fetch_failure_raises(self):
        from housing_list_search.extraction.pdf import extract_records_from_pdf

        with patch(
            "housing_list_search.extraction.pdf._fetch_pdf",
            side_effect=ValueError("Could not fetch PDF"),
        ):
            with pytest.raises(SourceFetchError, match="fetch failed"):
                extract_records_from_pdf("https://example.com/x.pdf", "Test")

    def test_non_pdf_body_raises(self):
        from housing_list_search.extraction.pdf import extract_records_from_pdf

        with patch(
            "housing_list_search.extraction.pdf._fetch_pdf",
            return_value=b"<html>not a pdf</html>",
        ):
            with pytest.raises(SourceFetchError, match="not a PDF"):
                extract_records_from_pdf("https://example.com/x.pdf", "Test")


class TestAltaDirectoryOnly:
    def test_empty_directory_raises(self):
        from housing_list_search.adapters import alta

        with patch.object(alta, "scrape_property_directory", return_value=[]):
            with pytest.raises(SourceFetchError, match="zero properties"):
                alta.scrape_alta("City of Palo Alto", "https://www.paloalto.gov/housing")

    def test_no_synthetic_program_rows(self):
        from housing_list_search.adapters import alta

        directory = [
            {
                "authority": "City of Palo Alto",
                "property_name": "Real Property",
                "url": "https://altahousing.org/property/real/",
                "source": "alta:property_directory",
            }
        ]
        with patch.object(alta, "scrape_property_directory", return_value=directory):
            records = alta.scrape_alta(
                "City of Palo Alto", "https://www.paloalto.gov/housing"
            )
        assert len(records) == 1
        assert records[0]["property_name"] == "Real Property"
        assert "BMR Ownership" not in records[0]["property_name"]
        assert "Affordable Housing Map" not in records[0]["property_name"]


class TestGisParseFail:
    def test_direct_geojson_bad_json_raises(self):
        from housing_list_search.adapters import gis_extraction as gis

        class FakeResp:
            def json(self):
                raise ValueError("not json")

        with patch(
            "housing_list_search.adapters.gis_extraction.polite_get",
            return_value=FakeResp(),
        ):
            with pytest.raises(SourceFetchError, match="JSON parse failed"):
                gis._parse_direct_geojson(
                    "https://example.com/data.geojson", "City of Cupertino"
                )


class TestRobotsRedirectPolicy:
    def test_robots_fetch_uses_redirect_policy_not_blind_follow(self):
        """#1081: robots.txt must not requests.get(..., allow_redirects=True)."""
        from housing_list_search.access import clear_robots_cache
        from housing_list_search.scraper import get_robots_entry

        clear_robots_cache()
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.headers = {}
        mock_resp.iter_content.return_value = [b"User-agent: *\nDisallow:\n"]

        with (
            patch(
                "housing_list_search.scraper.validate_http_url",
                side_effect=lambda url, **_: url,
            ),
            patch(
                "housing_list_search.scraper._request_with_redirect_policy",
                return_value=mock_resp,
            ) as mock_policy,
            patch("housing_list_search.scraper.requests.get") as mock_get,
        ):
            get_robots_entry("https://example.gov", "https://example.gov/robots.txt")
            mock_policy.assert_called_once()
            mock_get.assert_not_called()
        clear_robots_cache()

    def test_robots_redirect_to_private_blocked(self):
        from housing_list_search.access import clear_robots_cache
        from housing_list_search.scraper import RobotsEntry, _fetch_robots_entry

        clear_robots_cache()
        # First hop returns redirect to metadata; policy returns None
        with patch(
            "housing_list_search.scraper._request_with_redirect_policy",
            return_value=None,
        ):
            entry = _fetch_robots_entry("https://evil.example/robots.txt")
        assert isinstance(entry, RobotsEntry)
        assert entry.treat_as_allowed is True
        clear_robots_cache()
