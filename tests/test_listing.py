"""Tests for the canonical Listing persistence seam."""

from housing_list_search.extraction.pdf import HousingRecord
from housing_list_search.listing import (
    canonical_authority,
    canonicalize_listings,
    coerce_adapter_records,
    coerce_listing,
    listing_to_row,
)


class TestCanonicalizeListings:
    def test_skips_incomplete_rows(self):
        rows = canonicalize_listings(
            [
                {"authority": "City", "property_name": "OK", "url": "https://x"},
                {"authority": "", "property_name": "Skip", "url": ""},
            ]
        )
        assert len(rows) == 1
        assert rows[0]["property_name"] == "OK"
        assert rows[0]["url"] == "https://x"

    def test_surrogate_url_before_dedupe(self):
        rows = canonicalize_listings(
            [
                {
                    "authority": "SCCHA",
                    "property_name": "Oak Creek",
                    "address": "100 Oak St, San Jose, CA",
                    "url": "",
                },
            ]
        )
        assert rows[0]["url"].startswith("hls:addr:")


class TestCoerceListing:
    def test_dict_passthrough(self):
        d = {"property_name": "Oak", "authority": "Test"}
        assert coerce_listing(d) == d
        assert coerce_listing(d) is not d

    def test_housing_record_to_dict(self):
        rec = HousingRecord(authority="City", property_name="Cedar", document_url="https://x.pdf")
        d = coerce_listing(rec)
        assert d["property_name"] == "Cedar"
        assert d["document_url"] == "https://x.pdf"

    def test_coerce_adapter_records_mixes_dicts_and_housing_records(self):
        rec = HousingRecord(authority="City", property_name="Cedar", document_url="https://x.pdf")
        out = coerce_adapter_records([{"property_name": "A"}, rec])
        assert len(out) == 2
        assert out[0]["property_name"] == "A"
        assert out[1]["property_name"] == "Cedar"


class TestListingToRow:
    def test_authority_from_source_authority(self):
        row = listing_to_row(
            {"source_authority": "SCCHA", "property_name": "X", "url": "https://a"}
        )
        # Now canonicalized for seam stability (#983)
        assert row["authority"] == "Santa Clara County Housing Authority"

    def test_empty_url_uses_address_surrogate(self):
        row = listing_to_row(
            {
                "authority": "Morgan Hill",
                "property_name": "Fiesta Gardens",
                "url": "",
                "address": "123 Main St, Morgan Hill, CA 95037",
            }
        )
        assert row["url"].startswith("hls:addr:")

    def test_distinct_empty_url_records_do_not_collide(self):
        import os
        import tempfile

        from housing_list_search.db import DatabaseManager

        listings = [
            {
                "authority": "Morgan Hill",
                "property_name": "Fiesta Gardens",
                "url": "",
                "address": "",
            },
            {
                "authority": "Morgan Hill",
                "property_name": "De Rose Manor",
                "url": "",
                "address": "",
            },
        ]
        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseManager(os.path.join(tmp, "test.db"))
            result = db.upsert_listings(listings, run_id="run1")
            assert result["inserted"] == 2
            assert db.get_record_count() == 2

    def test_url_from_document_url(self):
        row = listing_to_row(
            {
                "authority": "Gilroy",
                "property_name": "Wheeler",
                "document_url": "https://gilroy.gov/doc.pdf",
            }
        )
        assert row["url"] == "https://gilroy.gov/doc.pdf"

    def test_listing_status_maps_to_status(self):
        row = listing_to_row(
            {
                "authority": "T",
                "property_name": "P",
                "url": "",
                "listing_status": "open",
            }
        )
        assert row["status"] == "Open"
        assert row["listing_status"] == "open"

    def test_eligibility_flags_list_joined(self):
        row = listing_to_row(
            {
                "authority": "T",
                "property_name": "P",
                "url": "",
                "eligibility_flags": ["senior", "low_income"],
            }
        )
        assert row["eligibility_flags"] == "senior|low_income"

    def test_notes_enriched_with_contact_fields(self):
        row = listing_to_row(
            {
                "authority": "T",
                "property_name": "P",
                "url": "",
                "phone": "408-555-0100",
                "bedrooms": "2 BR",
            }
        )
        assert "phone: 408-555-0100" in row["notes"]
        assert "br: 2 BR" in row["notes"]

    def test_db_upsert_uses_canonical_path(self):
        """listing_to_row output must satisfy upsert_listings required fields."""
        import os
        import tempfile

        from housing_list_search.db import DatabaseManager

        raw = {
            "authority": "Test City",
            "property_name": "Via Listing Module",
            "url": "https://example.com/via-listing",
            "listing_status": "waitlist",
            "eligibility_flags": ["low_income"],
        }
        with tempfile.TemporaryDirectory() as tmp:
            db = DatabaseManager(os.path.join(tmp, "test.db"))
            result = db.upsert_listings([raw], run_id="test-run")
            assert result["inserted"] == 1
            conn = db.connect()
            row = conn.execute(
                "SELECT status, listing_status, eligibility_flags FROM housing_records WHERE property_name=?",
                ("Via Listing Module",),
            ).fetchone()
            assert row[0] == "Waitlist Open"
            assert row[1] == "waitlist"
            assert row[2] == "low_income"


class TestListingSeamStability:
    """Regression tests for #983: identity seam, surrogates, canonical forms."""

    def test_canonical_authority_variants(self):
        assert canonical_authority("John Stewart Company (jsco.net portfolio)") == "John Stewart Company"
        assert canonical_authority("SCCHA Properties") == "Santa Clara County Housing Authority"
        assert canonical_authority("Housing Group - Campbell") == "Housing Group"
        assert canonical_authority("MidPen") == "MidPen Housing"

    def test_norm_address_improved_distinction(self):
        from housing_list_search.listing import norm_address
        a1 = norm_address("123 Main Street, San Jose CA 95112")
        a2 = norm_address("123 Main St. San Jose")
        # Should produce similar but we test non-empty and digit-containing
        assert a1 and "123" in a1
        assert a2 and len(a2) >= 6

    def test_surrogate_scoped_by_authority(self):
        # Same name, different authorities should not collide on prop surrogate
        row1 = listing_to_row({"authority": "City A", "property_name": "Oak Apts", "url": "", "address": ""})
        row2 = listing_to_row({"authority": "City B", "property_name": "Oak Apts", "url": "", "address": ""})
        assert row1["url"] != row2["url"]
        assert row1["url"].startswith("hls:prop:")
        assert row2["url"].startswith("hls:prop:")

    def test_canonical_listing_value_type_roundtrip(self):
        from housing_list_search.listing import CanonicalListing
        d = listing_to_row({"authority": "T", "property_name": "P", "url": "https://x"})
        # Construct minimal and verify to_dict preserves core + produces dict
        cl = CanonicalListing(authority=d["authority"], property_name=d["property_name"], url=d["url"])
        out = cl.to_dict()
        assert out["authority"] == "T"
        assert out["property_name"] == "P"
        assert isinstance(out, dict)
