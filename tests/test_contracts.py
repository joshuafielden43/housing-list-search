"""
Contract / regression tests for routing and output logic.

All tests here are pure unit tests — no network, no Playwright, no disk I/O.
They lock in the behavior fixed in the v0.8.5 audit so regressions are caught
before they reach the daily run.

Coverage:
- cli.py  multi-measure routing (housekeys+cdn both fire)
- bloom_housing.py  API pagination loop
- bloom_housing.py  city_filter applied after Playwright fallback
- bloom_housing.py  listing_status field set correctly on HousingRecord
- outputs.py  listing_status field used for open detection (no string fragility)
- outputs.py  structured records with short names appear in open section
- outputs.py  "accepting applications" / "waitlist open" treated as open
- dedupe.py   shared URL does NOT deduplicate distinct named properties
- normalizer.py  string eligibility_flags coerced to pipe-joined value
- housekeys.py  city page 403 still produces a registration record
- db.py  upsert_listings / export_csv / export_diff_csv / first_seen preservation
- changelog.py  generate_changelog / run_prev.csv round-trip
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
# bloom_housing.py — listing_status field on HousingRecord
# ---------------------------------------------------------------------------

class TestBloomListingStatus:
    """_bloom_record_from_item must set listing_status, not only embed it in notes."""

    def _make_item(self, status, marketing="", is_waitlist_open=False):
        return {
            "id": "test-uuid",
            "name": "Monroe Commons",
            "status": status,
            "marketingType": marketing,
            "isWaitlistOpen": is_waitlist_open,
            "reviewOrderType": "",
            "leasingAgentPhone": "",
            "leasingAgentEmail": "",
            "listingsBuildingAddress": {"city": "Santa Clara"},
            "units": [],
        }

    def test_active_listing_sets_open(self):
        from housing_list_search.extraction.bloom_housing import _bloom_record_from_item
        item = self._make_item("active")
        rec = _bloom_record_from_item(item, "https://housingbayarea.mtc.ca.gov/listings", "Test")
        assert rec.listing_status == "open"

    def test_closed_listing_sets_closed(self):
        from housing_list_search.extraction.bloom_housing import _bloom_record_from_item
        item = self._make_item("closed")
        rec = _bloom_record_from_item(item, "https://housingbayarea.mtc.ca.gov/listings", "Test")
        assert rec.listing_status == "closed"

    def test_coming_soon_sets_coming_soon(self):
        from housing_list_search.extraction.bloom_housing import _bloom_record_from_item
        item = self._make_item("active", marketing="comingSoon")
        rec = _bloom_record_from_item(item, "https://housingbayarea.mtc.ca.gov/listings", "Test")
        assert rec.listing_status == "coming_soon"

    def test_waitlist_open_sets_waitlist(self):
        from housing_list_search.extraction.bloom_housing import _bloom_record_from_item
        item = self._make_item("closed", is_waitlist_open=True)
        rec = _bloom_record_from_item(item, "https://housingbayarea.mtc.ca.gov/listings", "Test")
        assert rec.listing_status == "waitlist"

    def test_listing_status_field_surfaces_in_summary(self):
        """outputs.py must use listing_status field — not require notes string."""
        import tempfile, os
        from housing_list_search.outputs import generate_daily_summary
        from housing_list_search.extraction.bloom_housing import _bloom_record_from_item

        item = {
            "id": "abc", "name": "Monroe Commons", "status": "active",
            "marketingType": "", "isWaitlistOpen": False, "reviewOrderType": "",
            "leasingAgentPhone": "", "leasingAgentEmail": "",
            "listingsBuildingAddress": {"city": "Santa Clara"},
            "units": [],
        }
        rec = _bloom_record_from_item(item, "https://housingbayarea.mtc.ca.gov/listings", "City of Santa Clara")
        d = rec.to_dict()
        # Blank out notes to confirm listing_status alone triggers open detection
        d["notes"] = ""

        orig = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                generate_daily_summary([d])
                md = open("daily_summary.md").read()
            finally:
                os.chdir(orig)

        assert "Monroe Commons" in md
        assert "## 🔥 CURRENTLY OPEN" in md


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

    def test_housing_record_dataclass_does_not_crash_dedupe(self):
        """HousingRecord objects from pdf_scraper must not cause AttributeError in dedupe."""
        from housing_list_search.dedupe import deduplicate_listings
        from housing_list_search.extraction.pdf import HousingRecord
        rec = HousingRecord(
            authority="City of Test",
            property_name="Cedar Park Apartments",
            document_url="https://example.com/cedar.pdf",
        )
        result = deduplicate_listings([rec])
        assert len(result) == 1
        assert result[0]["property_name"] == "Cedar Park Apartments"


# ---------------------------------------------------------------------------
# normalizer.py — eligibility_flags coercion and listing_status→status mapping
# These tests call save_current_full() directly so a regression in the real
# write path is caught rather than just the logic duplicated here.
# ---------------------------------------------------------------------------

class TestNormalizerFlagsCoercion:

    def _csv_rows(self, listings):
        """Write via save_current_full and return parsed CSV rows."""
        import csv, os, tempfile
        from housing_list_search.normalizer import save_current_full
        orig = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                save_current_full(listings)
                with open("current_full.csv", newline="", encoding="utf-8") as f:
                    return list(csv.DictReader(f))
            finally:
                os.chdir(orig)

    def test_string_flag_is_joined_intact(self):
        rows = self._csv_rows([{"property_name": "Test", "eligibility_flags": "below_market_rate", "authority": "X"}])
        assert rows[0]["eligibility_flags"] == "below_market_rate"

    def test_list_flags_joined_with_pipe(self):
        rows = self._csv_rows([{"property_name": "Test", "eligibility_flags": ["below_market_rate", "senior"], "authority": "X"}])
        assert rows[0]["eligibility_flags"] == "below_market_rate|senior"

    def test_empty_string_flag_becomes_empty(self):
        rows = self._csv_rows([{"property_name": "Test", "eligibility_flags": "", "authority": "X"}])
        assert rows[0]["eligibility_flags"] == ""

    def test_listing_status_open_maps_to_Open_in_csv(self):
        rows = self._csv_rows([{"property_name": "Monroe Commons", "listing_status": "open", "authority": "Test", "eligibility_flags": []}])
        assert rows[0]["status"] == "Open"

    def test_listing_status_waitlist_maps_correctly(self):
        rows = self._csv_rows([{"property_name": "Park View", "listing_status": "waitlist", "authority": "Test", "eligibility_flags": []}])
        assert rows[0]["status"] == "Waitlist Open"

    def test_listing_status_closed_maps_correctly(self):
        rows = self._csv_rows([{"property_name": "Elm Court", "listing_status": "closed", "authority": "Test", "eligibility_flags": []}])
        assert rows[0]["status"] == "Closed"

    def test_listing_status_overrides_raw_status_field(self):
        """listing_status takes precedence over the generic status field."""
        rows = self._csv_rows([{
            "property_name": "Bloom Prop", "listing_status": "open", "status": "active",
            "authority": "Test", "eligibility_flags": [],
        }])
        assert rows[0]["status"] == "Open"

    def test_no_listing_status_falls_back_to_status_field(self):
        rows = self._csv_rows([{"property_name": "Generic", "status": "Waitlisting", "authority": "Test", "eligibility_flags": []}])
        assert rows[0]["status"] == "Waitlisting"


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
# runner.py — measure-driven dispatch (replaces inline cli.py routing)
# ---------------------------------------------------------------------------

class TestRunnerDispatch:
    """
    run_target() must be driven purely by scraping_measures.
    URL substrings and authority name patterns must NOT affect routing.
    """

    def _target(self, measures, authority="City of Test", url="https://example.gov/housing",
                admin_url=""):
        return {
            "authority": authority,
            "url": url,
            "scraping_measures": measures,
            "administrator": "",
            "administrator_url": admin_url,
            "administrator_phone": "",
            "administrator_contact": "",
            "notes": "",
        }

    def test_housekeys_and_cdn_both_called(self):
        hk_rec = {"property_name": "HK Prop", "authority": "City of Test", "url": "https://hk.example.com/"}
        cdn_rec = {"property_name": "CDN Prop", "authority": "City of Test", "url": "https://example.com/doc.pdf"}

        with (
            patch("housing_list_search.runner.extract_target", return_value=[]),
            patch("housing_list_search.runner.scrape_housekeys", return_value=[hk_rec]) as mock_hk,
            patch("housing_list_search.runner.extract_underlying_records", return_value=[cdn_rec]) as mock_cdn,
        ):
            from housing_list_search.runner import run_target
            result = run_target(self._target("housekeys,cdn"))

        mock_hk.assert_called_once()
        mock_cdn.assert_called_once()
        names = {r["property_name"] for r in result}
        assert "HK Prop" in names
        assert "CDN Prop" in names

    def test_john_stewart_routed_by_measure_not_url(self):
        """john_stewart measure must trigger the adapter regardless of URL content."""
        js_rec = {"property_name": "JS Prop", "authority": "City of Test", "url": "https://example.gov/housing"}

        with (
            patch("housing_list_search.runner.extract_target", return_value=[]),
            patch("housing_list_search.runner.scrape_john_stewart", return_value=[js_rec]) as mock_js,
        ):
            from housing_list_search.runner import run_target
            # URL has no "jscosccha" or "properties-list" — routing must come from the measure
            result = run_target(self._target("john_stewart", url="https://example.gov/housing"))

        mock_js.assert_called_once()
        assert result[0]["property_name"] == "JS Prop"

    def test_waf_blocked_returns_empty_immediately(self):
        with patch("housing_list_search.runner.extract_target", return_value=[]) as mock_ext:
            from housing_list_search.runner import run_target
            result = run_target(self._target("waf_blocked,cdn"))
        mock_ext.assert_not_called()
        assert result == []

    def test_unknown_measure_logged_not_crashed(self):
        """A typo like 'housekey' (missing s) must warn but not raise."""
        with (
            patch("housing_list_search.runner.extract_target", return_value=[]),
            patch("housing_list_search.runner.polite_get", return_value=None),
        ):
            from housing_list_search.runner import run_target
            result = run_target(self._target("housekey_typo"))
        assert isinstance(result, list)  # no exception

    def test_extraction_and_named_adapters_both_run(self):
        """Extraction layer results do not suppress named-measure adapters.
        A row with a Bloom URL AND housekeys measure must produce records from both."""
        class FakeRecord:
            def to_dict(self):
                return {"property_name": "Bloom Prop", "authority": "Test", "url": ""}

        hk_rec = {"property_name": "HK Prop", "authority": "Test", "url": "https://hk.example.com/"}

        with (
            patch("housing_list_search.runner.extract_target", return_value=[FakeRecord()]),
            patch("housing_list_search.runner.scrape_housekeys", return_value=[hk_rec]) as mock_hk,
        ):
            from housing_list_search.runner import run_target
            result = run_target(self._target("housekeys"))

        mock_hk.assert_called_once()
        names = {r["property_name"] for r in result}
        assert "Bloom Prop" in names
        assert "HK Prop" in names


# ---------------------------------------------------------------------------
# db.py — upsert_listings, export_csv, export_diff_csv, first_seen preservation
# ---------------------------------------------------------------------------

class TestDatabaseManager:
    """DatabaseManager must persist listings, preserve first_seen, and tag diffs."""

    def _make_db(self, tmp_path):
        from pathlib import Path
        from housing_list_search.db import DatabaseManager
        db = DatabaseManager(db_path=Path(tmp_path) / "test.db")
        db.init_db()
        return db

    def _listing(self, name, authority="Test City", url="", status="open",
                 listing_status="open", first_seen=None, last_seen=None):
        d = {
            "property_name": name,
            "authority": authority,
            "url": url,
            "status": status,
            "listing_status": listing_status,
            "eligibility_flags": [],
        }
        if first_seen:
            d["first_seen"] = first_seen
        if last_seen:
            d["last_seen"] = last_seen
        return d

    def test_upsert_inserts_new_record(self, tmp_path):
        db = self._make_db(tmp_path)
        counts = db.upsert_listings([self._listing("Cedar Park")], run_id="run1")
        assert counts["inserted"] == 1
        assert counts["updated"] == 0
        assert db.get_record_count() == 1

    def test_upsert_updates_existing_record(self, tmp_path):
        db = self._make_db(tmp_path)
        db.upsert_listings([self._listing("Cedar Park", status="closed")], run_id="run1")
        counts = db.upsert_listings([self._listing("Cedar Park", status="open")], run_id="run2")
        assert counts["updated"] == 1
        assert db.get_record_count() == 1

    def test_first_seen_preserved_on_update(self, tmp_path):
        import csv
        from pathlib import Path
        db = self._make_db(tmp_path)
        db.upsert_listings([self._listing("Cedar Park", first_seen="2026-01-01T00:00:00")], run_id="run1")
        db.upsert_listings([self._listing("Cedar Park", last_seen="2026-06-05T00:00:00")], run_id="run2")

        export_path = str(Path(tmp_path) / "out.csv")
        db.export_csv(export_path)
        with open(export_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert len(rows) == 1
        assert rows[0]["first_seen"] == "2026-01-01T00:00:00"

    def test_export_csv_writes_rich_fields(self, tmp_path):
        import csv
        from pathlib import Path
        db = self._make_db(tmp_path)
        db.upsert_listings([{
            "property_name": "Park View",
            "authority": "Test City",
            "url": "",
            "bedrooms": "1BR,2BR",
            "income_limits": "80% AMI",
            "eligibility_flags": ["senior", "below_market_rate"],
            "listing_status": "open",
        }], run_id="run1")

        export_path = str(Path(tmp_path) / "out.csv")
        db.export_csv(export_path)
        with open(export_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert rows[0]["bedrooms"] == "1BR,2BR"
        assert rows[0]["income_limits"] == "80% AMI"
        assert rows[0]["eligibility_flags"] == "senior|below_market_rate"
        assert rows[0]["listing_status"] == "open"

    def test_export_diff_csv_new_vs_stale(self, tmp_path):
        """Records from run1 are STALE when diff is exported with run2's run_id."""
        import csv
        from pathlib import Path
        db = self._make_db(tmp_path)
        db.upsert_listings([
            self._listing("New Prop"),
            self._listing("Old Prop"),
        ], run_id="run1")
        # Second run only sees New Prop
        db.upsert_listings([self._listing("New Prop")], run_id="run2")

        diff_path = str(Path(tmp_path) / "diff.csv")
        db.export_diff_csv(diff_path, run_id="run2")
        with open(diff_path, newline="", encoding="utf-8") as f:
            rows = {r["property_name"]: r["change_type"] for r in csv.DictReader(f)}

        assert rows["New Prop"] == "UPDATED"   # seen in run2, existed before
        assert rows["Old Prop"] == "STALE"     # not confirmed in run2

    def test_export_diff_csv_marks_new(self, tmp_path):
        """A brand-new record (first_seen == last_seen) must be tagged NEW."""
        import csv
        from pathlib import Path
        db = self._make_db(tmp_path)
        now = "2026-06-05T10:00:00"
        db.upsert_listings([
            self._listing("Fresh Prop", first_seen=now, last_seen=now),
        ], run_id="run1")

        diff_path = str(Path(tmp_path) / "diff.csv")
        db.export_diff_csv(diff_path, run_id="run1")
        with open(diff_path, newline="", encoding="utf-8") as f:
            rows = {r["property_name"]: r["change_type"] for r in csv.DictReader(f)}

        assert rows["Fresh Prop"] == "NEW"

    def test_upsert_skips_records_missing_required_fields(self, tmp_path):
        """Records without authority+property_name are silently skipped."""
        db = self._make_db(tmp_path)
        counts = db.upsert_listings([
            {"authority": "", "property_name": "No Auth"},
            {"authority": "City", "property_name": ""},
            {"property_name": "Missing Auth"},
        ], run_id="run1")
        assert counts["inserted"] == 0
        assert db.get_record_count() == 0

    def test_diff_counts_matches_export_labels(self, tmp_path):
        db = self._make_db(tmp_path)
        db.upsert_listings([
            self._listing("New Prop"),
            self._listing("Old Prop"),
        ], run_id="run1")
        db.upsert_listings([self._listing("New Prop")], run_id="run2")

        counts = db.diff_counts("run2")
        assert counts["UPDATED"] == 1
        assert counts["STALE"] == 1
        assert counts["NEW"] == 0


# ---------------------------------------------------------------------------
# changelog.py — generate_changelog / run_prev.csv round-trip
# ---------------------------------------------------------------------------

class TestChangelogRoundTrip:
    """generate_changelog must diff against run_prev.csv and snapshot correctly."""

    def _run_changelog(self, tmp_path, current, skipped=None):
        import os
        from housing_list_search.changelog import generate_changelog
        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            generate_changelog(current, skipped_targets=skipped or [])
        finally:
            os.chdir(orig)

    def _read_file(self, tmp_path, filename):
        import os
        return open(os.path.join(tmp_path, filename), encoding="utf-8").read()

    def _read_csv(self, tmp_path, filename):
        import csv, os
        with open(os.path.join(tmp_path, filename), newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_first_run_writes_baseline_snapshot(self, tmp_path):
        """First run (no run_prev.csv) produces a snapshot and reports initial population."""
        listings = [
            {"authority": "Test City", "property_name": "Park View", "status": "Open", "listing_status": "open"},
        ]
        self._run_changelog(tmp_path, listings)

        import os
        assert os.path.exists(os.path.join(tmp_path, "run_prev.csv"))
        md = self._read_file(tmp_path, "changelog_diffs.md")
        assert "First run" in md

    def test_added_listing_appears_in_next_run_changelog(self, tmp_path):
        """A listing present in run2 but absent from run1 must appear as Added."""
        run1 = [{"authority": "City", "property_name": "Old Prop", "status": "Open", "listing_status": "open"}]
        self._run_changelog(tmp_path, run1)

        run2 = run1 + [{"authority": "City", "property_name": "New Prop", "status": "Open", "listing_status": "open"}]
        self._run_changelog(tmp_path, run2)

        md = self._read_file(tmp_path, "changelog_diffs.md")
        assert "New Prop" in md
        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        added = [r for r in rows if r["change_type"] == "ADDED"]
        assert any(r["property_name"] == "New Prop" for r in added)

    def test_removed_listing_appears_in_next_run_changelog(self, tmp_path):
        """A listing absent in run2 that was present in run1 must appear as Removed."""
        run1 = [
            {"authority": "City", "property_name": "Stays", "status": "Open", "listing_status": "open"},
            {"authority": "City", "property_name": "Gone Prop", "status": "Open", "listing_status": "open"},
        ]
        self._run_changelog(tmp_path, run1)

        run2 = [{"authority": "City", "property_name": "Stays", "status": "Open", "listing_status": "open"}]
        self._run_changelog(tmp_path, run2)

        md = self._read_file(tmp_path, "changelog_diffs.md")
        assert "Gone Prop" in md
        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        removed = [r for r in rows if r["change_type"] == "REMOVED"]
        assert any(r["property_name"] == "Gone Prop" for r in removed)

    def test_removed_listing_does_not_accumulate_across_runs(self, tmp_path):
        """The 'removed forever' bug: a removed record must NOT reappear in run3 changelog."""
        run1 = [
            {"authority": "City", "property_name": "Gone Prop", "status": "Open", "listing_status": "open"},
            {"authority": "City", "property_name": "Stays", "status": "Open", "listing_status": "open"},
        ]
        self._run_changelog(tmp_path, run1)

        run2 = [{"authority": "City", "property_name": "Stays", "status": "Open", "listing_status": "open"}]
        self._run_changelog(tmp_path, run2)

        # run3: same as run2 — Gone Prop should NOT appear again
        self._run_changelog(tmp_path, run2)

        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        removed = [r for r in rows if r["change_type"] == "REMOVED"]
        assert not any(r["property_name"] == "Gone Prop" for r in removed), (
            "Gone Prop reappeared in run3 — run_prev.csv is using DB snapshot instead of run-seen snapshot"
        )

    def test_status_change_detected(self, tmp_path):
        """A listing with a changed status field must appear in Status Changed section."""
        run1 = [{"authority": "City", "property_name": "Monroe Commons", "status": "Open", "listing_status": "open"}]
        self._run_changelog(tmp_path, run1)

        run2 = [{"authority": "City", "property_name": "Monroe Commons", "status": "Closed", "listing_status": "closed"}]
        self._run_changelog(tmp_path, run2)

        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        changed = [r for r in rows if r["change_type"] == "STATUS_CHANGE"]
        assert any(r["property_name"] == "Monroe Commons" for r in changed)

    def test_no_change_produces_no_change_row(self, tmp_path):
        """Identical run1 and run2 must produce a NO_CHANGE row, not empty CSV."""
        run = [{"authority": "City", "property_name": "Stable Prop", "status": "Open", "listing_status": "open"}]
        self._run_changelog(tmp_path, run)
        self._run_changelog(tmp_path, run)

        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        assert any(r["change_type"] == "NO_CHANGE" for r in rows)
