"""
Contract / regression tests for routing and output logic.

All tests here are pure unit tests — no network, no Playwright, no disk I/O.
They lock in the behavior fixed in the v0.8.5 audit so regressions are caught
before they reach the daily run.

Coverage:
- cli.py  multi-measure routing (housekeys+civicplus both fire)
- bloom_housing.py  API pagination loop
- bloom_housing.py  city_filter applied after Playwright fallback
- bloom_housing.py  listing_status field set correctly on HousingRecord
- outputs.py  listing_status field used for open detection (no string fragility)
- outputs.py  structured records with short names appear in open section
- outputs.py  "accepting applications" / "waitlist open" treated as open
- dedupe.py   shared URL does NOT deduplicate distinct named properties
- listing_to_row + export_csv: string eligibility_flags coerced to pipe-joined value
- housekeys.py  city page 403 still produces a registration record
- db.py  upsert_listings / export_csv / export_diff_csv / first_seen preservation
- changelog.py  generate_changelog / run_prev.csv round-trip
"""

from __future__ import annotations

import json
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

    def _run(self, listings, **kwargs):
        """Run generate_daily_summary and return the written markdown."""
        import os
        import tempfile

        from housing_list_search.outputs import generate_daily_summary

        # Write to a temp dir so we never touch the real daily_summary.md
        orig_dir = os.getcwd()
        with tempfile.TemporaryDirectory() as tmpdir:
            os.chdir(tmpdir)
            try:
                generate_daily_summary(listings, **kwargs)
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
        listings = [
            _listing(
                "Oakwood Terrace Apartments",
                notes="accepting applications; lottery",
                source="bloom:x",
            )
        ]
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
        listings = [
            _listing(
                "Closed Towers",
                status="closed",
                notes="closed — not currently accepting applications",
                source="bloom:x",
            )
        ]
        md = self._run(listings)
        assert "Closed Towers" not in md or "CURRENTLY OPEN" not in md

    def test_nav_link_excluded_even_when_open(self):
        listings = [
            _listing("Quick links to housing", status="Open", notes="", source="generic:scrape")
        ]
        md = self._run(listings)
        assert "Quick links to housing" not in md

    def test_unstructured_name_under_5_chars_excluded(self):
        # source without a colon = generic/unstructured; short names should be filtered
        listings = [_listing("BMR", status="Open", notes="", source="generic")]
        md = self._run(listings)
        assert "## 🔥 CURRENTLY OPEN" not in md

    def test_coverage_breakdown_shows_portal_vs_property(self):
        listings = [
            _listing("Oak Manor", source="midpen:find_housing", addr="1 Oak St"),
            {
                "property_name": "City of Morgan Hill BMR Homeownership Program (via HouseKeys)",
                "authority": "City of Morgan Hill",
                "source": "housekeys:city_of_morgan_hill",
                "administrator": "HouseKeys",
                "url": "https://www.housekeys1.com/",
                "status": "Registration Required",
                "notes": "",
            },
        ]
        md = self._run(listings)
        assert "Coverage breakdown" in md
        assert "Property inventory:** 1" in md
        assert "Portal pointers:** 1" in md
        assert "UEO-style property count:** 1" in md
        assert "Portal pointers (not property inventory)" in md

    def test_closed_records_without_opens_are_explained(self):
        listings = [
            _listing(f"Closed Property {i}", status="closed", notes="closed", source="bloom:x")
            for i in range(11)
        ]
        md = self._run(listings)
        assert "11 extracted" in md
        assert "No open or accepting listings" in md
        assert "registration portals" in md
        assert "No currently open lists detected" not in md
        assert "This run produced **11 listings**" not in md

    def test_run_status_shows_target_failures(self):
        md = self._run(
            [_listing("Open Homes", status="Open", source="bloom:x")],
            run_stats={
                "targets_attempted": 15,
                "targets_succeeded": 12,
                "failed_authorities": ["City A", "City B", "City C"],
            },
        )
        assert "## Run Status" in md
        assert "12 succeeded, 3 failed (of 15 attempted)" in md
        assert "City A, City B, City C" in md
        assert "SCRAPE_FAILED" in md

    def test_run_status_shows_clean_run(self):
        md = self._run(
            [_listing("Open Homes", status="Open", source="bloom:x")],
            run_stats={
                "targets_attempted": 18,
                "targets_succeeded": 18,
                "failed_authorities": [],
            },
        )
        assert "18 succeeded (of 18 attempted)" in md
        assert "failed" not in md.lower().split("run status", 1)[-1][:120]

    def test_needs_review_shows_suspicious_zero(self):
        md = self._run(
            [],
            run_stats={
                "targets_attempted": 18,
                "targets_succeeded": 18,
                "failed_authorities": [],
                "suspicious_zero_authorities": ["City of Campbell", "MidPen Housing"],
            },
        )
        assert "## Needs Review" in md
        assert "Suspicious zero" in md
        assert "City of Campbell, MidPen Housing" in md

    def test_needs_review_omitted_when_clean(self):
        md = self._run(
            [_listing("Open Homes", status="Open", source="bloom:x")],
            run_stats={
                "targets_attempted": 18,
                "targets_succeeded": 18,
                "failed_authorities": [],
                "suspicious_zero_authorities": [],
            },
        )
        assert "Needs Review" not in md

    def test_needs_review_shows_reverification_due(self):
        md = self._run(
            [],
            run_stats={
                "targets_attempted": 18,
                "targets_succeeded": 18,
                "failed_authorities": [],
                "suspicious_zero_authorities": [],
                "reverification_due_authorities": ["City of Campbell"],
            },
        )
        assert "## Needs Review" in md
        assert "Reverification due" in md
        assert "City of Campbell" in md

    def test_integrity_summary_shows_stale_and_scrape_failed(self):
        md = self._run(
            [],
            run_stats={
                "targets_attempted": 18,
                "targets_succeeded": 18,
                "failed_authorities": [],
                "stale_n": 12,
                "scrape_failed_n": 3,
                "stale_warn_threshold": 5,
            },
        )
        assert "Integrity signals" in md
        assert "STALE" in md
        assert "SCRAPE_FAILED" in md
        assert "db_manage.py prune" in md

    def test_open_listings_show_more_cue_when_truncated(self):
        listings = [_listing(f"Open Home {i}", status="Open", source="bloom:x") for i in range(105)]
        md = self._run(listings)
        assert "105 open or accepting applications" in md
        assert "+ 5 more open listing" in md


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
        import os
        import tempfile

        from housing_list_search.extraction.bloom_housing import _bloom_record_from_item
        from housing_list_search.outputs import generate_daily_summary

        item = {
            "id": "abc",
            "name": "Monroe Commons",
            "status": "active",
            "marketingType": "",
            "isWaitlistOpen": False,
            "reviewOrderType": "",
            "leasingAgentPhone": "",
            "leasingAgentEmail": "",
            "listingsBuildingAddress": {"city": "Santa Clara"},
            "units": [],
        }
        rec = _bloom_record_from_item(
            item, "https://housingbayarea.mtc.ca.gov/listings", "City of Santa Clara"
        )
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
            {
                "property_name": "Fiesta Gardens",
                "authority": "Morgan Hill",
                "url": shared_url,
                "address": "",
                "confidence": "medium",
            },
            {
                "property_name": "De Rose Manor",
                "authority": "Morgan Hill",
                "url": shared_url,
                "address": "",
                "confidence": "medium",
            },
            {
                "property_name": "La Colina",
                "authority": "Morgan Hill",
                "url": shared_url,
                "address": "",
                "confidence": "medium",
            },
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
            {
                "property_name": "Oak Creek",
                "authority": "SCCHA",
                "address": "100 Oak St, San Jose, CA",
                "url": "",
                "confidence": "high",
            },
            {
                "property_name": "Oak Creek",
                "authority": "SJ Portal",
                "address": "100 Oak St, San Jose, CA",
                "url": "",
                "confidence": "medium",
            },
        ]
        result = deduplicate_listings(records)
        assert len(result) == 1
        assert result[0]["authority"] == "SCCHA"  # higher confidence kept

    def test_housing_record_dataclass_does_not_crash_dedupe(self):
        """HousingRecord objects from extraction layer must not cause AttributeError in dedupe."""
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
# listing_to_row + export_csv coverage for flag coercion and status semantics
# (replaced legacy normalizer.py direct writer tests)
# ---------------------------------------------------------------------------


class TestListingToRowAndExportFlagsStatus:
    def test_listing_to_row_joins_eligibility_flags(self):
        from housing_list_search.listing import listing_to_row

        row = listing_to_row(
            {"property_name": "Test", "eligibility_flags": ["below_market_rate", "senior"], "authority": "X"}
        )
        assert row["eligibility_flags"] == "below_market_rate|senior"

        row2 = listing_to_row(
            {"property_name": "Test", "eligibility_flags": "below_market_rate", "authority": "X"}
        )
        assert row2["eligibility_flags"] == "below_market_rate"

    def test_export_csv_preserves_flag_and_status_semantics(self, tmp_path):
        """Production path via DB export (was normalizer + save_current_full)."""
        import csv

        from housing_list_search.db import DatabaseManager
        from housing_list_search.listing import listing_to_row

        db = DatabaseManager(db_path=tmp_path / "t.db")
        db.init_db()

        raw = {
            "property_name": "Monroe Commons",
            "authority": "City of Test",
            "url": "https://example.com/m",
            "listing_status": "open",
            "eligibility_flags": ["senior", "below_market_rate"],
            "bedrooms": "1BR,2BR",
            "income_limits": "80% AMI",
        }
        db.upsert_listings([listing_to_row(raw)])

        export_path = str(tmp_path / "out.csv")
        db.export_csv(export_path)
        with open(export_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert rows[0]["eligibility_flags"] == "senior|below_market_rate"
        assert rows[0]["status"] == "Open"
        assert rows[0]["listing_status"] == "open"


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

    def _make_page(self, items, total, status_code=200):
        payload = {"items": items, "meta": {"totalItems": total}}
        content = json.dumps(payload).encode()
        return MagicMock(
            **{
                "status_code": status_code,
                "content": content,
                "json.return_value": payload,
            }
        )

    def test_two_pages_fetched_when_first_page_full(self):
        # Build page1 with exactly 100 items (the hard-coded page_size) so the
        # "len(page_items) < page_size" short-circuit does NOT fire; the loop
        # must continue to page 2 because totalItems=101.
        page1_items = [
            {"id": str(i), "name": f"Prop {i}", "listingsBuildingAddress": {"city": "San Jose"}}
            for i in range(100)
        ]
        page2_items = [
            {"id": "100", "name": "Prop 100", "listingsBuildingAddress": {"city": "San Jose"}}
        ]

        responses = [self._make_page(page1_items, 101), self._make_page(page2_items, 101)]

        import housing_list_search.extraction.bloom_housing as bh

        orig_api_instances = bh._API_INSTANCES
        bh._API_INSTANCES = {
            "housingbayarea.mtc.ca.gov": {
                "jurisdictionname": "Test",
                "endpoint": "https://housingbayarea.mtc.ca.gov/api/adapter/listings/combined",
            }
        }
        try:
            with patch("requests.post", side_effect=responses) as mock_post:
                open_items, _ = bh._fetch_via_api(
                    "https://housingbayarea.mtc.ca.gov/listings",
                    city_filter="",
                )
        finally:
            bh._API_INSTANCES = orig_api_instances

        assert mock_post.call_count == 2
        assert len(open_items) == 101

    def test_http_201_accepted(self):
        import housing_list_search.extraction.bloom_housing as bh

        orig = bh._API_INSTANCES
        bh._API_INSTANCES = {
            "housingbayarea.mtc.ca.gov": {
                "jurisdictionname": "Bay Area",
                "endpoint": "https://housingbayarea.mtc.ca.gov/api/adapter/listings/combined",
            }
        }
        try:
            with patch(
                "requests.post",
                return_value=self._make_page(
                    [
                        {
                            "id": "1",
                            "name": "Monroe Commons",
                            "listingsBuildingAddress": {"city": "Santa Clara"},
                        }
                    ],
                    1,
                    status_code=201,
                ),
            ):
                open_items, _ = bh._fetch_via_api(
                    "https://housingbayarea.mtc.ca.gov/listings",
                    city_filter="Santa Clara",
                )
        finally:
            bh._API_INSTANCES = orig
        assert len(open_items) == 1

    def test_city_filter_applied_after_all_pages(self):
        items_p1 = [
            {
                "id": "1",
                "name": "Monroe Commons",
                "listingsBuildingAddress": {"city": "Santa Clara"},
            },
            {"id": "2", "name": "San Jose Prop", "listingsBuildingAddress": {"city": "San Jose"}},
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
            with patch("requests.post", return_value=self._make_page(items_p1, 2)):
                open_items, _ = bh._fetch_via_api(
                    "https://housingbayarea.mtc.ca.gov/listings",
                    city_filter="Santa Clara",
                )
        finally:
            bh._API_INSTANCES = orig

        assert len(open_items) == 1
        assert open_items[0]["name"] == "Monroe Commons"

    def test_partial_pagination_returns_collected_items_on_failure(self):
        """Mid-pagination HTTP failure returns items collected so far (#760)."""
        page1_items = [
            {"id": str(i), "name": f"Prop {i}", "listingsBuildingAddress": {"city": "San Jose"}}
            for i in range(100)
        ]
        responses = [
            self._make_page(page1_items, 101),
            self._make_page([], 101, status_code=500),
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
            with patch("requests.post", side_effect=responses):
                open_items, closed_items = bh._fetch_via_api(
                    "https://housingbayarea.mtc.ca.gov/listings",
                    city_filter="",
                )
        finally:
            bh._API_INSTANCES = orig

        assert len(open_items) == 100
        assert open_items[0]["name"] == "Prop 0"
        assert closed_items == []


# ---------------------------------------------------------------------------
# bloom_housing.py — city_filter applied after Playwright fallback
# ---------------------------------------------------------------------------


class TestBloomPlaywrightCityFilter:
    def test_city_filter_applied_after_playwright(self):
        import housing_list_search.extraction.bloom_housing as bh

        playwright_open = [
            {
                "id": "a",
                "name": "Santa Clara Prop",
                "listingsBuildingAddress": {"city": "Santa Clara"},
            },
            {"id": "b", "name": "Sunnyvale Prop", "listingsBuildingAddress": {"city": "Sunnyvale"}},
            {"id": "c", "name": "San Jose Prop", "listingsBuildingAddress": {"city": "San Jose"}},
        ]

        with (
            patch.object(bh, "_fetch_via_ssr", return_value=([], [])),
            patch.object(bh, "_fetch_via_api", return_value=([], [])),
            patch.object(bh, "_fetch_via_playwright", return_value=(playwright_open, [])),
        ):
            records = bh.extract_bloom_housing_listings(
                "https://unknown-bloom.example.gov/listings",
                authority="City of Santa Clara",
                city_filter="Santa Clara",
            )

        assert len(records) == 1
        assert records[0].property_name == "Santa Clara Prop"


# ---------------------------------------------------------------------------
# extraction/__init__.py — city_filter derivation for MTC Doorway targets
# ---------------------------------------------------------------------------


class TestCityFilterDerivation:
    """extract_target must strip parenthetical qualifiers from authority names
    so city_filter exactly matches listingsBuildingAddress.city in Bloom."""

    def _city_filter_for(self, authority: str) -> str:
        """Replicate the derivation logic from extraction/__init__.py."""
        import re

        cf = authority.replace("City of ", "").replace("Town of ", "")
        cf = re.sub(r"\s*\(.*\)\s*$", "", cf).strip()
        return cf

    def test_parenthetical_qualifier_stripped(self):
        """'City of Santa Clara (rentals via MTC Doorway)' must yield 'Santa Clara'."""
        assert (
            self._city_filter_for("City of Santa Clara (rentals via MTC Doorway)") == "Santa Clara"
        )

    def test_plain_city_of_prefix_stripped(self):
        assert self._city_filter_for("City of Santa Clara") == "Santa Clara"

    def test_town_of_prefix_stripped(self):
        assert self._city_filter_for("Town of Los Gatos") == "Los Gatos"

    def test_no_prefix_unchanged(self):
        assert self._city_filter_for("MTC Doorway Bay Area") == "MTC Doorway Bay Area"

    def test_extract_target_passes_clean_filter(self):
        """extract_target must call extract_bloom_housing_listings with the bare
        city name, not the full authority string including parenthetical."""
        from unittest.mock import patch

        with patch(
            "housing_list_search.extraction.bloom_housing.extract_bloom_housing_listings",
            return_value=[],
        ) as mock_bloom:
            from housing_list_search.extraction import extract_target

            extract_target(
                "https://housingbayarea.mtc.ca.gov/listings",
                authority="City of Santa Clara (rentals via MTC Doorway)",
            )

        assert mock_bloom.called
        _, kwargs = mock_bloom.call_args
        assert kwargs.get("city_filter") == "Santa Clara", (
            f"Expected city_filter='Santa Clara', got {kwargs.get('city_filter')!r}"
        )


# ---------------------------------------------------------------------------
# outputs.py — daily_summary KeyError on missing url field
# ---------------------------------------------------------------------------


class TestDailySummaryUrlFallback:
    """generate_daily_summary must not crash when a listing lacks a 'url' key."""

    def _run(self, listings):
        import os
        import tempfile

        from housing_list_search.outputs import generate_daily_summary

        orig = os.getcwd()
        with tempfile.TemporaryDirectory() as tmp:
            os.chdir(tmp)
            try:
                generate_daily_summary(listings)
                return open("daily_summary.md", encoding="utf-8").read()
            finally:
                os.chdir(orig)

    def test_listing_with_source_url_does_not_crash(self):
        listing = {
            "property_name": "Wheeler Manor",
            "authority": "City of Gilroy",
            "listing_status": "open",
            "status": "Open",
            "notes": "",
            "source": "civicplus:gilroy",
            "source_url": "https://www.cityofgilroy.org/DocumentCenter/View/16932",
            "deadline": "",
            # intentionally no 'url' key
        }
        md = self._run([listing])
        assert "Wheeler Manor" in md

    def test_listing_with_document_url_does_not_crash(self):
        listing = {
            "property_name": "Cedar Park Apts",
            "authority": "City of Test",
            "listing_status": "open",
            "status": "Open",
            "notes": "",
            "source": "civicplus:test",
            "document_url": "https://example.com/cedar.pdf",
            "deadline": "",
        }
        md = self._run([listing])
        assert "Cedar Park Apts" in md

    def test_listing_with_no_url_fields_does_not_crash(self):
        listing = {
            "property_name": "No Link Property",
            "authority": "City of Test",
            "listing_status": "open",
            "status": "Open",
            "notes": "",
            "source": "civicplus:test",
            "deadline": "",
        }
        md = self._run([listing])
        assert "No Link Property" in md


# ---------------------------------------------------------------------------
# dispatch.py — measure-driven dispatch (collapsed seam)
# ---------------------------------------------------------------------------


class TestRunnerDispatch:
    """
    scrape_target() must be driven purely by scraping_measures.
    URL substrings and authority name patterns must NOT affect routing.
    """

    def _target(
        self, measures, authority="City of Test", url="https://example.gov/housing", admin_url=""
    ):
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

    def test_housekeys_and_civicplus_both_called(self):
        hk_rec = {
            "property_name": "HK Prop",
            "authority": "City of Test",
            "url": "https://hk.example.com/",
        }
        cp_rec = {
            "property_name": "CivicPlus Prop",
            "authority": "City of Test",
            "url": "https://example.com/doc.pdf",
        }

        with (
            patch("housing_list_search.dispatch._run_url_extractors", return_value=[]),
            patch(
                "housing_list_search.dispatch._MEASURE_HANDLERS",
                {
                    "housekeys": lambda ctx: [hk_rec],
                    "civicplus": lambda ctx: [cp_rec],
                },
            ),
        ):
            from housing_list_search.dispatch import scrape_target

            outcome = scrape_target(self._target("housekeys,civicplus"))
            result = outcome.records

        names = {r["property_name"] for r in result}
        assert "HK Prop" in names
        assert "CivicPlus Prop" in names

    def test_john_stewart_routed_by_measure_not_url(self):
        """john_stewart measure must trigger the adapter regardless of URL content."""
        js_rec = {
            "property_name": "JS Prop",
            "authority": "City of Test",
            "url": "https://example.gov/housing",
        }

        with (
            patch("housing_list_search.dispatch._run_url_extractors", return_value=[]),
            patch(
                "housing_list_search.dispatch._MEASURE_HANDLERS",
                {
                    "john_stewart": lambda ctx: [js_rec],
                },
            ),
        ):
            from housing_list_search.dispatch import scrape_target

            # URL has no "jscosccha" or "properties-list" — routing must come from the measure
            outcome = scrape_target(self._target("john_stewart", url="https://example.gov/housing"))
            result = outcome.records
        assert result[0]["property_name"] == "JS Prop"

    def test_waf_blocked_returns_empty_immediately(self):
        with patch("housing_list_search.dispatch._run_url_extractors", return_value=[]) as mock_ext:
            from housing_list_search.dispatch import scrape_target

            outcome = scrape_target(self._target("waf_blocked,civicplus"))
            result = outcome.records
        mock_ext.assert_not_called()
        assert result == []

    def test_unknown_measure_logged_not_crashed(self):
        """A typo like 'housekey' (missing s) must warn but not raise."""
        with patch("housing_list_search.dispatch._run_url_extractors", return_value=[]):
            from housing_list_search.dispatch import scrape_target

            outcome = scrape_target(self._target("housekey_typo"))
            result = outcome.records
        assert isinstance(result, list)  # no exception

    def test_extraction_and_named_adapters_both_run(self):
        """Extraction layer results do not suppress named-measure adapters.
        A row with bloom + housekeys measures must produce records from both."""
        bloom_rec = {"property_name": "Bloom Prop", "authority": "Test", "url": ""}
        hk_rec = {"property_name": "HK Prop", "authority": "Test", "url": "https://hk.example.com/"}

        with (
            patch("housing_list_search.dispatch._run_url_extractors", return_value=[bloom_rec]),
            patch(
                "housing_list_search.dispatch._MEASURE_HANDLERS",
                {
                    "housekeys": lambda ctx: [hk_rec],
                },
            ),
        ):
            from housing_list_search.dispatch import scrape_target

            outcome = scrape_target(
                self._target(
                    "bloom,housekeys",
                    url="https://housing.sanjoseca.gov/listings",
                )
            )
            result = outcome.records

        names = {r["property_name"] for r in result}
        assert "Bloom Prop" in names
        assert "HK Prop" in names

    def test_bloom_requires_measure(self):
        """Bloom host alone must not trigger extraction without bloom measure."""
        with patch(
            "housing_list_search.extraction.bloom_housing.extract_bloom_for_target"
        ) as mock_bloom:
            from housing_list_search.dispatch import scrape_target

            scrape_target(
                self._target(
                    "housekeys",
                    url="https://housing.sanjoseca.gov/listings",
                )
            )
        mock_bloom.assert_not_called()

    def test_adapter_error_appends_to_failures_list(self):
        def _boom(_ctx):
            raise RuntimeError("boom")

        with (
            patch("housing_list_search.dispatch._run_url_extractors", return_value=[]),
            patch("housing_list_search.dispatch._MEASURE_HANDLERS", {"housekeys": _boom}),
        ):
            from housing_list_search.dispatch import scrape_target

            failures: list[str] = []
            outcome = scrape_target(self._target("housekeys"))
            result = outcome.records
            if outcome.had_error:
                auth = "City of Test"
                if auth not in failures:
                    failures.append(auth)

        assert result == []
        assert failures == ["City of Test"]


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

    def _listing(
        self,
        name,
        authority="Test City",
        url="",
        status="open",
        listing_status="open",
        first_seen=None,
        last_seen=None,
    ):
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
        db.upsert_listings(
            [self._listing("Cedar Park", first_seen="2026-01-01T00:00:00")], run_id="run1"
        )
        db.upsert_listings(
            [self._listing("Cedar Park", last_seen="2026-06-05T00:00:00")], run_id="run2"
        )

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
        db.upsert_listings(
            [
                {
                    "property_name": "Park View",
                    "authority": "Test City",
                    "url": "",
                    "bedrooms": "1BR,2BR",
                    "income_limits": "80% AMI",
                    "eligibility_flags": ["senior", "below_market_rate"],
                    "listing_status": "open",
                }
            ],
            run_id="run1",
        )

        export_path = str(Path(tmp_path) / "out.csv")
        db.export_csv(export_path)
        with open(export_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert rows[0]["bedrooms"] == "1BR,2BR"
        assert rows[0]["income_limits"] == "80% AMI"
        assert rows[0]["eligibility_flags"] == "senior|below_market_rate"
        assert rows[0]["listing_status"] == "open"
        assert rows[0]["status"] == "Open"

    def test_export_csv_maps_listing_status_to_display_status(self, tmp_path):
        """Production DB export must preserve status semantics (via listing_to_row)."""
        import csv
        from pathlib import Path

        db = self._make_db(tmp_path)
        db.upsert_listings(
            [
                {
                    "property_name": "Monroe Commons",
                    "authority": "City of Santa Clara",
                    "url": "https://housingbayarea.mtc.ca.gov/listing/abc",
                    "status": "",
                    "listing_status": "open",
                }
            ],
            run_id="run1",
        )

        export_path = str(Path(tmp_path) / "out.csv")
        db.export_csv(export_path)
        with open(export_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert rows[0]["status"] == "Open"

    def test_export_diff_csv_new_vs_stale(self, tmp_path):
        """Records from run1 are STALE when diff is exported with run2's run_id."""
        import csv
        from pathlib import Path

        db = self._make_db(tmp_path)
        db.upsert_listings(
            [
                self._listing("New Prop"),
                self._listing("Old Prop"),
            ],
            run_id="run1",
        )
        # Second run only sees New Prop
        db.upsert_listings([self._listing("New Prop")], run_id="run2")

        diff_path = str(Path(tmp_path) / "diff.csv")
        db.export_diff_csv(diff_path, run_id="run2")
        with open(diff_path, newline="", encoding="utf-8") as f:
            rows = {r["property_name"]: r["change_type"] for r in csv.DictReader(f)}

        assert rows["New Prop"] == "UPDATED"  # seen in run2, existed before
        assert rows["Old Prop"] == "STALE"  # not confirmed in run2

    def test_export_diff_csv_marks_new(self, tmp_path):
        """A brand-new record (first_seen == last_seen) must be tagged NEW."""
        import csv
        from pathlib import Path

        db = self._make_db(tmp_path)
        now = "2026-06-05T10:00:00"
        db.upsert_listings(
            [
                self._listing("Fresh Prop", first_seen=now, last_seen=now),
            ],
            run_id="run1",
        )

        diff_path = str(Path(tmp_path) / "diff.csv")
        db.export_diff_csv(diff_path, run_id="run1")
        with open(diff_path, newline="", encoding="utf-8") as f:
            rows = {r["property_name"]: r["change_type"] for r in csv.DictReader(f)}

        assert rows["Fresh Prop"] == "NEW"

    def test_export_diff_csv_marks_inserted_record_new_even_with_imported_first_seen(
        self, tmp_path
    ):
        """run_id-based NEW detection must not depend on first_seen == last_seen."""
        import csv
        from pathlib import Path

        db = self._make_db(tmp_path)
        db.upsert_listings(
            [
                self._listing("Imported Fresh Prop", first_seen="2026-01-01T00:00:00"),
            ],
            run_id="run1",
        )

        diff_path = str(Path(tmp_path) / "diff.csv")
        db.export_diff_csv(diff_path, run_id="run1")
        with open(diff_path, newline="", encoding="utf-8") as f:
            rows = {r["property_name"]: r["change_type"] for r in csv.DictReader(f)}

        assert rows["Imported Fresh Prop"] == "NEW"

    def test_upsert_skips_records_missing_required_fields(self, tmp_path):
        """Records without authority+property_name are silently skipped."""
        db = self._make_db(tmp_path)
        counts = db.upsert_listings(
            [
                {"authority": "", "property_name": "No Auth"},
                {"authority": "City", "property_name": ""},
                {"property_name": "Missing Auth"},
            ],
            run_id="run1",
        )
        assert counts["inserted"] == 0
        assert db.get_record_count() == 0

    def test_diff_counts_matches_export_labels(self, tmp_path):
        db = self._make_db(tmp_path)
        db.upsert_listings(
            [
                self._listing("New Prop"),
                self._listing("Old Prop"),
            ],
            run_id="run1",
        )
        db.upsert_listings([self._listing("New Prop")], run_id="run2")

        counts = db.diff_counts("run2")
        assert counts["UPDATED"] == 1
        assert counts["STALE"] == 1
        assert counts["NEW"] == 0

    def test_partial_run_diff_scope_excludes_unselected_authorities(self, tmp_path):
        """A --target-style diff must not mark unrelated authorities as STALE."""
        import csv
        from pathlib import Path

        db = self._make_db(tmp_path)
        db.upsert_listings(
            [
                self._listing("A Prop", authority="City A"),
                self._listing("B Prop", authority="City B"),
            ],
            run_id="full",
        )
        db.upsert_listings(
            [
                self._listing("A Prop", authority="City A"),
            ],
            run_id="partial",
        )

        diff_path = str(Path(tmp_path) / "diff.csv")
        db.export_diff_csv(diff_path, run_id="partial", authorities=["City A"])
        with open(diff_path, newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))

        assert {r["source_authority"] for r in rows} == {"City A"}
        assert {r["property_name"]: r["change_type"] for r in rows} == {"A Prop": "UPDATED"}
        assert db.diff_counts("partial", authorities=["City A"]) == {
            "NEW": 0,
            "UPDATED": 1,
            "SCRAPE_FAILED": 0,
            "STALE": 0,
        }


# ---------------------------------------------------------------------------
# changelog.py — generate_changelog / run_prev.csv round-trip
# ---------------------------------------------------------------------------


class TestChangelogRoundTrip:
    """generate_changelog must project from diff.csv and snapshot run_prev for STATUS_CHANGE."""

    _run_counter = 0

    def setup_method(self):
        TestChangelogRoundTrip._run_counter = 0

    def _run_changelog(self, tmp_path, current, skipped=None, diff_rows=None):
        import csv
        import os

        from housing_list_search.changelog import generate_changelog

        TestChangelogRoundTrip._run_counter += 1
        run_id = f"run{TestChangelogRoundTrip._run_counter}"
        previous_run_id = (
            f"run{TestChangelogRoundTrip._run_counter - 1}"
            if TestChangelogRoundTrip._run_counter > 1
            else None
        )

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            if diff_rows is not None:
                fieldnames = [
                    "change_type",
                    "source_authority",
                    "property_name",
                    "url",
                    "last_run_id",
                ]
                with open("diff.csv", "w", newline="", encoding="utf-8") as f:
                    writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                    writer.writeheader()
                    writer.writerows(diff_rows)
            generate_changelog(
                current,
                skipped_targets=skipped or [],
                run_id=run_id,
                previous_run_id=previous_run_id,
            )
        finally:
            os.chdir(orig)

    def _read_file(self, tmp_path, filename):
        import os

        return open(os.path.join(tmp_path, filename), encoding="utf-8").read()

    def _read_csv(self, tmp_path, filename):
        import csv
        import os

        with open(os.path.join(tmp_path, filename), newline="", encoding="utf-8") as f:
            return list(csv.DictReader(f))

    def test_first_run_writes_baseline_snapshot(self, tmp_path):
        """First run (no run_prev.csv) produces a snapshot and reports initial population."""
        listings = [
            {
                "authority": "Test City",
                "property_name": "Park View",
                "status": "Open",
                "listing_status": "open",
            },
        ]
        self._run_changelog(tmp_path, listings)

        import os

        assert os.path.exists(os.path.join(tmp_path, "run_prev.csv"))
        md = self._read_file(tmp_path, "changelog_diffs.md")
        assert "First run" in md

    def test_added_listing_appears_in_next_run_changelog(self, tmp_path):
        """A listing present in run2 but absent from run1 must appear as Added."""
        run1 = [
            {
                "authority": "City",
                "property_name": "Old Prop",
                "status": "Open",
                "listing_status": "open",
            }
        ]
        self._run_changelog(tmp_path, run1)

        run2 = run1 + [
            {
                "authority": "City",
                "property_name": "New Prop",
                "status": "Open",
                "listing_status": "open",
            }
        ]
        self._run_changelog(
            tmp_path,
            run2,
            diff_rows=[
                {
                    "change_type": "NEW",
                    "source_authority": "City",
                    "property_name": "New Prop",
                    "url": "hls:prop:New Prop",
                },
            ],
        )

        md = self._read_file(tmp_path, "changelog_diffs.md")
        assert "New Prop" in md
        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        added = [r for r in rows if r["change_type"] == "ADDED"]
        assert any(r["property_name"] == "New Prop" for r in added)

    def test_removed_listing_appears_in_next_run_changelog(self, tmp_path):
        """A listing absent in run2 that was present in run1 must appear as Removed."""
        run1 = [
            {
                "authority": "City",
                "property_name": "Stays",
                "status": "Open",
                "listing_status": "open",
            },
            {
                "authority": "City",
                "property_name": "Gone Prop",
                "status": "Open",
                "listing_status": "open",
            },
        ]
        self._run_changelog(tmp_path, run1)

        run2 = [
            {
                "authority": "City",
                "property_name": "Stays",
                "status": "Open",
                "listing_status": "open",
            }
        ]
        self._run_changelog(
            tmp_path,
            run2,
            diff_rows=[
                {
                    "change_type": "STALE",
                    "source_authority": "City",
                    "property_name": "Gone Prop",
                    "url": "hls:prop:Gone Prop",
                    "last_run_id": "run1",
                },
            ],
        )

        md = self._read_file(tmp_path, "changelog_diffs.md")
        assert "Gone Prop" in md
        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        removed = [r for r in rows if r["change_type"] == "REMOVED"]
        assert any(r["property_name"] == "Gone Prop" for r in removed)

    def test_removed_listing_does_not_accumulate_across_runs(self, tmp_path):
        """The 'removed forever' bug: a removed record must NOT reappear in run3 changelog."""
        run1 = [
            {
                "authority": "City",
                "property_name": "Gone Prop",
                "status": "Open",
                "listing_status": "open",
            },
            {
                "authority": "City",
                "property_name": "Stays",
                "status": "Open",
                "listing_status": "open",
            },
        ]
        self._run_changelog(tmp_path, run1)

        run2 = [
            {
                "authority": "City",
                "property_name": "Stays",
                "status": "Open",
                "listing_status": "open",
            }
        ]
        self._run_changelog(
            tmp_path,
            run2,
            diff_rows=[
                {
                    "change_type": "STALE",
                    "source_authority": "City",
                    "property_name": "Gone Prop",
                    "url": "hls:prop:Gone Prop",
                    "last_run_id": "run1",
                },
            ],
        )

        # run3: same as run2 — Gone Prop should NOT appear again
        self._run_changelog(tmp_path, run2, diff_rows=[])

        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        removed = [r for r in rows if r["change_type"] == "REMOVED"]
        assert not any(r["property_name"] == "Gone Prop" for r in removed), (
            "Gone Prop reappeared in run3 — run_prev.csv is using DB snapshot instead of run-seen snapshot"
        )

    def test_status_change_detected(self, tmp_path):
        """A listing with a changed status field must appear in Status Changed section."""
        run1 = [
            {
                "authority": "City",
                "property_name": "Monroe Commons",
                "status": "Open",
                "listing_status": "open",
            }
        ]
        self._run_changelog(tmp_path, run1)

        run2 = [
            {
                "authority": "City",
                "property_name": "Monroe Commons",
                "status": "Closed",
                "listing_status": "closed",
            }
        ]
        self._run_changelog(tmp_path, run2)

        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        changed = [r for r in rows if r["change_type"] == "STATUS_CHANGE"]
        assert any(r["property_name"] == "Monroe Commons" for r in changed)

    def test_listing_status_only_change_detected(self, tmp_path):
        """Bloom-style rows may have blank status; listing_status must still drive changelog."""
        run1 = [
            {
                "authority": "City",
                "property_name": "Monroe Commons",
                "status": "",
                "listing_status": "open",
            }
        ]
        self._run_changelog(tmp_path, run1)

        run2 = [
            {
                "authority": "City",
                "property_name": "Monroe Commons",
                "status": "",
                "listing_status": "closed",
            }
        ]
        self._run_changelog(tmp_path, run2)

        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        changed = [r for r in rows if r["change_type"] == "STATUS_CHANGE"]
        assert any(r["property_name"] == "Monroe Commons" for r in changed)

    def test_no_change_produces_no_change_row(self, tmp_path):
        """Identical run1 and run2 must produce a NO_CHANGE row, not empty CSV."""
        run = [
            {
                "authority": "City",
                "property_name": "Stable Prop",
                "status": "Open",
                "listing_status": "open",
            }
        ]
        self._run_changelog(tmp_path, run)
        self._run_changelog(tmp_path, run)

        rows = self._read_csv(tmp_path, "changelog_diffs.csv")
        assert any(r["change_type"] == "NO_CHANGE" for r in rows)


# ---------------------------------------------------------------------------
# cli.py — --target partial runs must not corrupt global run baselines
# ---------------------------------------------------------------------------


class TestCliTargetRun:
    def test_target_run_scopes_diff_and_preserves_run_prev(self, tmp_path):
        import csv
        import os
        import sys
        from pathlib import Path

        from housing_list_search.cli import main
        from housing_list_search.db import DatabaseManager

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()
            db.upsert_listings(
                [
                    {
                        "authority": "City A",
                        "property_name": "A Prop",
                        "url": "https://a",
                        "listing_status": "open",
                    },
                    {
                        "authority": "City B",
                        "property_name": "B Prop",
                        "url": "https://b",
                        "listing_status": "open",
                    },
                ],
                run_id="full",
            )

            run_prev = "source_authority,property_name,status,listing_status\nCity A,A Prop,Open,open\nCity B,B Prop,Open,open\n"
            Path("run_prev.csv").write_text(run_prev, encoding="utf-8")

            targets = [
                {"authority": "City A", "url": "https://a", "scraping_measures": "", "notes": ""},
                {"authority": "City B", "url": "https://b", "scraping_measures": "", "notes": ""},
            ]

            with (
                patch.object(sys, "argv", ["main.py", "--run", "--target", "City A"]),
                patch("housing_list_search.registry.load_targets_to_db", return_value=None),
                patch("housing_list_search.registry.get_active_targets", return_value=targets),
                patch("housing_list_search.registry.get_skipped_targets", return_value=[]),
                patch(
                    "housing_list_search.dispatch.scrape_target",
                    return_value=[
                        {
                            "authority": "City A",
                            "property_name": "A Prop",
                            "url": "https://a",
                            "listing_status": "open",
                        }
                    ],
                ),
            ):
                main()

            with open("diff.csv", newline="", encoding="utf-8") as f:
                diff_rows = list(csv.DictReader(f))
            with open("changelog_diffs.csv", newline="", encoding="utf-8") as f:
                changelog_rows = list(csv.DictReader(f))

            assert {r["source_authority"] for r in diff_rows} == {"City A"}
            assert not any(r["source_authority"] == "City B" for r in diff_rows)
            assert Path("run_prev.csv").read_text(encoding="utf-8") == run_prev
            assert changelog_rows[0]["change_type"] == "PARTIAL_RUN"
        finally:
            os.chdir(orig)

    def test_target_run_preserves_staff_daily_summary(self, tmp_path):
        import os
        import sys
        from pathlib import Path

        from housing_list_search.cli import main
        from housing_list_search.db import DatabaseManager
        from housing_list_search.outputs import (
            PARTIAL_DAILY_SUMMARY_PATH,
            STAFF_DAILY_SUMMARY_PATH,
        )

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            staff_summary = "# Staff summary from last full run\n"
            Path(STAFF_DAILY_SUMMARY_PATH).write_text(staff_summary, encoding="utf-8")

            targets = [
                {"authority": "City A", "url": "https://a", "scraping_measures": "", "notes": ""},
            ]

            with (
                patch.object(sys, "argv", ["main.py", "--run", "--target", "City A"]),
                patch("housing_list_search.registry.load_targets_to_db", return_value=None),
                patch("housing_list_search.registry.get_active_targets", return_value=targets),
                patch("housing_list_search.registry.get_skipped_targets", return_value=[]),
                patch(
                    "housing_list_search.dispatch.scrape_target",
                    return_value=[
                        {
                            "authority": "City A",
                            "property_name": "A Prop",
                            "url": "https://a",
                            "listing_status": "open",
                            "status": "Open",
                        }
                    ],
                ),
            ):
                main()

            assert Path(STAFF_DAILY_SUMMARY_PATH).read_text(encoding="utf-8") == staff_summary
            partial_md = Path(PARTIAL_DAILY_SUMMARY_PATH).read_text(encoding="utf-8")
            assert "A Prop" in partial_md
        finally:
            os.chdir(orig)

    def test_run_exits_nonzero_when_target_fails(self, tmp_path):
        import os
        import sys
        from pathlib import Path

        from housing_list_search.cli import main
        from housing_list_search.db import DatabaseManager

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            targets = [
                {
                    "authority": "City A",
                    "url": "https://a",
                    "scraping_measures": "housekeys",
                    "notes": "",
                },
            ]

            with (
                patch.object(sys, "argv", ["main.py", "--run", "--target", "City A"]),
                patch("housing_list_search.registry.load_targets_to_db", return_value=None),
                patch("housing_list_search.registry.get_active_targets", return_value=targets),
                patch("housing_list_search.registry.get_skipped_targets", return_value=[]),
                patch(
                    "housing_list_search.dispatch.scrape_target",
                    side_effect=RuntimeError("adapter down"),
                ),
                pytest.raises(SystemExit) as excinfo,
            ):
                main()

            assert excinfo.value.code == 1
        finally:
            os.chdir(orig)
