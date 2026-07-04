"""Tests for property vs portal coverage classification."""

from housing_list_search.coverage import classify_record_kind, summarize_coverage


class TestClassifyRecordKind:
    def test_housekeys_source_is_portal(self):
        item = {
            "property_name": "City of Morgan Hill BMR Homeownership Program (via HouseKeys)",
            "source": "housekeys:city_of_morgan_hill",
            "administrator": "HouseKeys",
        }
        assert classify_record_kind(item) == "portal"

    def test_john_stewart_property(self):
        item = {
            "property_name": "Monroe Commons",
            "source": "john_stewart:jsco_portfolio",
            "address": "Santa Clara, CA",
        }
        assert classify_record_kind(item) == "property"

    def test_civicplus_pdf_header_is_program(self):
        item = {"property_name": "HOUSING ASSISTANCE", "source": "civicplus:los_gatos"}
        assert classify_record_kind(item) == "program"

    def test_bloom_listing_is_property(self):
        item = {
            "property_name": "Brooks House Senior Apartments",
            "source": "bloom:san_jose",
            "address": "123 Main St",
        }
        assert classify_record_kind(item) == "property"


class TestSummarizeCoverage:
    def test_mixed_run_counts(self):
        listings = [
            {"property_name": "Oak Manor", "source": "midpen:find_housing", "address": "1 Oak St"},
            {
                "property_name": "Milpitas BMR (via HouseKeys)",
                "source": "housekeys:city_of_milpitas",
                "administrator": "HouseKeys",
            },
            {"property_name": "HOUSING ASSISTANCE", "source": "civicplus:gilroy"},
        ]
        s = summarize_coverage(listings)
        assert s.total == 3
        assert s.property_count == 1
        assert s.portal_count == 1
        assert s.program_count == 1
        assert s.property_inventory_count == 1
