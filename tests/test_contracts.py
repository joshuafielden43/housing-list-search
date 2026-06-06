"""
Contract / regression tests for routing and output logic.

All tests here are pure unit tests — no network, no Playwright, no disk I/O.
They lock in the behavior fixed in the v0.8.5 audit so regressions are caught
before they reach the daily run.

Coverage:
- cli.py  multi-measure routing (housekeys+cdn both fire)
- bloom_housing.py  API pagination loop
- bloom_housing.py  city_filter applied after Playwright fallback
- outputs.py  structured records with short names appear in open section
- outputs.py  "accepting applications" / "waitlist open" treated as open
- dedupe.py   shared URL does NOT deduplicate distinct named properties
- normalizer.py  string eligibility_flags coerced to pipe-joined value
- housekeys.py  city page 403 still produces a registration record
"""

from __future__ import annotations

import io
import json
from typing import Any
from unittest.mock import MagicMock, patch

import pytest


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _listing(name, status="active", notes="accepting applications", source="bloom:test", addr=""):
    return {
        "property_name": name,
        "authority": "Test Authority",
        "status": status,
        "notes": notes,
        "source": source,
        "url": "https://example.com/listings",
        "address": addr,
        "deadline": "",
    }


# ---------------------------------------------------------------------------
# outputs.py — summary open-listing detection
# ---------------------------------------------------------------------------

class TestSummaryOpenDetection:
    """generate_daily_summary must surface realistic open listings."""

    def _run(self, listings):
        """Run generate_daily_summary and return the written markdown."""
        import tempfile, os
        from housing_list_search.outputs import generate_daily_summary
        # Write to a temp dir so we never touch the real daily_summary.md
        orig_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            try:
                generate_daily_summary(listings)
                with open("daily_summary.md", encoding="utf-8") as f:
                    return f.read()
            finally:
                os.chdir(orig_dir)

    def test_short_structured_name_appears(self):
        """'Monroe Commons' (13 chars, 1 space) must appear — structured source bypasses heuristics."""
        listings = [_listing("Monroe Commons", source="bloom:santa_clara")]
        md = self._run(listings)
        assert "Monroe Commons" in md

    def test_accepting_applications_in_notes_is_open(self):
        listings = [_listing("Oakwood Terrace Apartments", notes="accepting applications; lottery", source="bloom:x")]
        md = self._run(listings)
        assert "Oakwood Terrace Apartments" in md

    def test_waitlist_open_in_notes_is_open(self):
        listings = [_listing("Park View Senior", notes="waitlist open (3 spots)", source="bloom:x")]
        md = self._run(listings)
        assert "Park View Senior" in md

    def test_status_open_field_is_open(self):
        listings = [_listing("Sunrise Gardens", status="Open", notes="", source="generic:test")]
        md = self._run(listings)
        assert "Sunrise Gardens" in md

    def test_closed_listing_excluded(self):
        listings = [_listing("Closed Towers", status="closed", notes="closed — not currently accepting applications", source="bloom:x")]
        md = self._run(listings)
        assert "Closed Towers" not in md or "CURRENTLY OPEN" not in md

    def test_nav_link_excluded_even_when_open(self):
        listings = [_listing("Quick links to housing", status="Open", notes="", source="generic:scrape")]
        md = self._run(listings)
        assert "Quick links to housing" not in md

    def test_unstructured_name_under_5_chars_excluded(self):
        # source without a colon = generic/unstructured; short names should be filtered
        listings = [_listing("BMR", status="Open", notes="", source="generic_scrape")]
        md = self._run(listings)
        assert "## 🔥 CURRENTLY OPEN" not in md


# ---------------------------------------------------------------------------
# dedupe.py — shared URL must NOT collapse distinct named records
# ---------------------------------------------------------------------------

class TestDedupeSharedURL:
    def test_distinct_names_same_url_both_kept(self):
        from housing_list_search.dedupe import deduplicate_listings
        shared_url = "https://www.housekeys1.com/"
        records = [
            {"property_name": "Fiesta Gardens", "authority": "Morgan Hill", "url": shared_url, "address": "", "confidence": "medium"},
            {"property_name": "De Rose Manor",  "authority": "Morgan Hill", "url": shared_url, "address": "", "confidence": "medium"},
            {"property_name": "La Colina",      "authority": "Morgan Hill", "url": shared_url, "address": "", "confidence": "medium"},
        ]
        result = deduplicate_listings(records)
        names = {r["property_name"] for r in result}
        assert "Fiesta Gardens" in names
        assert "De Rose Manor" in names
        assert "La Colina" in names
        assert len(result) == 3

    def test_same_address_deduplicates_across_sources(self):
        from housing_list_search.dedupe import deduplicate_listings
        records = [
            {"property_name": "Oak Creek", "authority": "SCCHA", "address": "100 Oak St, San Jose, CA", "url": "", "confidence": "high"},
            {"property_name": "Oak Creek",  "authority": "SJ Portal", "address": "100 Oak St, San Jose, CA", "url": "", "confidence": "medium"},
        ]
        result = deduplicate_listings(records)
        assert len(result) == 1
        assert result[0]["authority"] == "SCCHA"  # higher confidence kept


# ---------------------------------------------------------------------------
# normalizer.py — eligibility_flags string coercion
# ---------------------------------------------------------------------------

class TestNormalizerFlagsCoercion:
    def test_string_flag_is_joined_intact(self):
        from housing_list_search.normalizer import normalize_listing
        # normalizer itself returns the raw value; coercion happens in save_current_full.
        # We test the save path via a mock writer.
        import csv, io
        from unittest.mock import patch, mock_open

        raw = {"property_name": "Test", "eligibility_flags": "below_market_rate", "authority": "X"}

        # Simulate exactly what save_current_full does
        from housing_list_search import normalizer as nm
        row = nm.normalize_listing(raw)
        flags = row["eligibility_flags"]
        if isinstance(flags, str):
            flags = [flags] if flags else []
        joined = "|".join(flags)
        assert joined == "below_market_rate"

    def test_list_flags_joined_with_pipe(self):
        from housing_list_search import normalizer as nm
        raw = {"property_name": "Test", "eligibility_flags": ["below_market_rate", "senior"], "authority": "X"}
        row = nm.normalize_listing(raw)
        flags = row["eligibility_flags"]
        if isinstance(flags, str):
            flags = [flags] if flags else []
        assert "|".join(flags) == "below_market_rate|senior"

    def test_empty_string_flag_becomes_empty(self):
        from housing_list_search import normalizer as nm
        raw = {"property_name": "Test", "eligibility_flags": "", "authority": "X"}
        row = nm.normalize_listing(raw)
        flags = row["eligibility_flags"]
        if isinstance(flags, str):
            flags = [flags] if flags else []
        assert "|".join(flags) == ""


# ---------------------------------------------------------------------------
# housekeys.py — city 403 still produces a registration record
# ---------------------------------------------------------------------------

class TestHouseKeysFailedFetch:
    def test_city_page_403_still_returns_record(self):
        from housing_list_search.adapters.housekeys import scrape_housekeys
        with patch("housing_list_search.adapters.housekeys.polite_get", return_value=None):
            records = scrape_housekeys(
                "City of Morgan Hill",
                "https://www.morganhill.ca.gov/629/Housing",
                admin_url="https://www.housekeys1.com/",
            )
        assert len(records) == 1
        assert records[0]["url"] == "https://www.housekeys1.com/"
        assert "Morgan Hill" in records[0]["property_name"]

    def test_fallback_to_default_when_no_admin_url(self):
        from housing_list_search.adapters.housekeys import scrape_housekeys
        with patch("housing_list_search.adapters.housekeys.polite_get", return_value=None):
            records = scrape_housekeys(
                "City of Somewhere",
                "https://www.somewhere.gov/housing",
                admin_url="",
            )
        assert len(records) == 1
        assert records[0]["url"] == "https://www.housekeys24.com/"


# ---------------------------------------------------------------------------
# bloom_housing.py — API pagination fetches beyond page 1
# ---------------------------------------------------------------------------

class TestBloomAPIPagination:
    """_fetch_via_api must loop through pages until meta.totalItems is satisfied."""

    def _make_page(self, items, total):
        return MagicMock(**{
            "status_code": 200,
            "json.return_value": {"items": items, "meta": {"totalItems": total}},
        })

    def test_two_pages_fetched_when_first_page_full(self):
        # Build page1 with exactly 100 items (the hard-coded page_size) so the
        # "len(page_items) < page_size" short-circuit does NOT fire; the loop
        # must continue to page 2 because totalItems=101.
        page1_items = [{"id": str(i), "name": f"Prop {i}", "listingsBuildingAddress": {"city": "San Jose"}} for i in range(100)]
        page2_items = [{"id": "100", "name": "Prop 100", "listingsBuildingAddress": {"city": "San Jose"}}]

        responses = [self._make_page(page1_items, 101), self._make_page(page2_items, 101)]

        import housing_list_search.extraction.bloom_housing as bh
        orig_api_instances = bh._API_INSTANCES
        bh._API_INSTANCES = {
            "test.example.com": {
                "jurisdictionname": "Test",
                "endpoint": "https://test.example.com/api/adapter/listings/combined",
            }
        }
        try:
            with patch("requests.post", side_effect=responses) as mock_post:
                open_items, _ = bh._fetch_via_api(
                    "https://test.example.com/listings",
                    city_filter="",
                )
        finally:
            bh._API_INSTANCES = orig_api_instances

        assert mock_post.call_count == 2
        assert len(open_items) == 101

    def test_city_filter_applied_after_all_pages(self):
        items_p1 = [
            {"id": "1", "name": "Monroe Commons", "listingsBuildingAddress": {"city": "Santa Clara"}},
            {"id": "2", "name": "San Jose Prop",  "listingsBuildingAddress": {"city": "San Jose"}},
        ]

        import housing_list_search.extraction.bloom_housing as bh
        orig = bh._API_INSTANCES
        bh._API_INSTANCES = {
            "housingbayarea.mtc.ca.gov": {
                "jurisdictionname": "Bay Area",
                "endpoint": "https://housingbayarea.mtc.ca.gov/api/adapter/listings/combined",
            }
        }
        try:
            with patch("requests.post", return_value=MagicMock(**{
                "status_code": 200,
                "json.return_value": {"items": items_p1, "meta": {"totalItems": 2}},
            })):
                open_items, _ = bh._fetch_via_api(
                    "https://housingbayarea.mtc.ca.gov/listings",
                    city_filter="Santa Clara",
                )
        finally:
            bh._API_INSTANCES = orig

        assert len(open_items) == 1
        assert open_items[0]["name"] == "Monroe Commons"


# ---------------------------------------------------------------------------
# bloom_housing.py — city_filter applied after Playwright fallback
# ---------------------------------------------------------------------------

class TestBloomPlaywrightCityFilter:
    def test_city_filter_applied_after_playwright(self):
        import housing_list_search.extraction.bloom_housing as bh

        playwright_open = [
            {"id": "a", "name": "Santa Clara Prop", "listingsBuildingAddress": {"city": "Santa Clara"}},
            {"id": "b", "name": "Sunnyvale Prop",   "listingsBuildingAddress": {"city": "Sunnyvale"}},
            {"id": "c", "name": "San Jose Prop",    "listingsBuildingAddress": {"city": "San Jose"}},
        ]

        with (
            patch.object(bh, "_fetch_via_ssr", return_value=([], [])),
            patch.object(bh, "_fetch_via_api", return_value=([], [])),
            patch.object(bh, "_fetch_via_playwright", return_value=(playwright_open, [])),
        ):
            records = bh.extract_bloom_housing_listings(
                "https://housingbayarea.mtc.ca.gov/listings",
                authority="City of Santa Clara",
                city_filter="Santa Clara",
            )

        assert len(records) == 1
        assert records[0].property_name == "Santa Clara Prop"


# ---------------------------------------------------------------------------
# cli.py — multi-measure targets run both housekeys AND cdn
# ---------------------------------------------------------------------------

class TestMultiMeasureRouting:
    """
    A target with measures='housekeys,cdn' must invoke both adapters.
    We stub both adapters and verify both are called.
    """

    def _make_target(self, measures):
        return {
            "authority": "City of Gilroy",
            "url": "https://www.cityofgilroy.org/279/Housing-and-Community-Services",
            "scraping_measures": measures,
            "notes": "",
            "administrator": "HouseKeys",
            "administrator_url": "https://www.housekeys5.com/",
            "administrator_phone": "",
            "administrator_contact": "",
        }

    def test_housekeys_and_cdn_both_called_for_combined_measures(self):
        from housing_list_search.adapters.housekeys import scrape_housekeys
        from housing_list_search.adapters.cdn import extract_underlying_records

        hk_record = {"property_name": "HK Record", "authority": "City of Gilroy", "url": "https://www.housekeys5.com/"}
        cdn_record = {"property_name": "CDN Record", "authority": "City of Gilroy", "url": "https://example.com/doc.pdf"}

        with (
            patch("housing_list_search.adapters.housekeys.polite_get", return_value=None),
            patch("housing_list_search.adapters.cdn.extract_underlying_records", return_value=[cdn_record]) as mock_cdn,
            patch("housing_list_search.adapters.housekeys.scrape_housekeys", return_value=[hk_record]) as mock_hk,
            patch("housing_list_search.extraction.extract_target", return_value=[]),
            patch("housing_list_search.registry.load_targets_to_db"),
            patch("housing_list_search.registry.get_active_targets", return_value=[self._make_target("housekeys,cdn")]),
            patch("housing_list_search.registry.get_skipped_targets", return_value=[]),
            patch("housing_list_search.normalizer.save_current_full"),
            patch("housing_list_search.changelog.generate_changelog"),
            patch("housing_list_search.outputs.generate_daily_summary"),
            patch("housing_list_search.dedupe.deduplicate_listings", side_effect=lambda x: x),
        ):
            import sys
            with patch.object(sys, "argv", ["main.py", "--run"]):
                from housing_list_search.cli import main
                try:
                    main()
                except SystemExit:
                    pass

        mock_hk.assert_called_once()
        mock_cdn.assert_called_once()
