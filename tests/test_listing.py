"""Tests for the canonical Listing persistence seam."""

from housing_list_search.extraction.pdf import HousingRecord
from housing_list_search.listing import coerce_listing, listing_to_row


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


class TestListingToRow:
    def test_authority_from_source_authority(self):
        row = listing_to_row({"source_authority": "SCCHA", "property_name": "X", "url": "https://a"})
        assert row["authority"] == "SCCHA"

    def test_url_from_document_url(self):
        row = listing_to_row({
            "authority": "Gilroy",
            "property_name": "Wheeler",
            "document_url": "https://gilroy.gov/doc.pdf",
        })
        assert row["url"] == "https://gilroy.gov/doc.pdf"

    def test_listing_status_maps_to_status(self):
        row = listing_to_row({
            "authority": "T",
            "property_name": "P",
            "url": "",
            "listing_status": "open",
        })
        assert row["status"] == "Open"
        assert row["listing_status"] == "open"

    def test_eligibility_flags_list_joined(self):
        row = listing_to_row({
            "authority": "T",
            "property_name": "P",
            "url": "",
            "eligibility_flags": ["senior", "low_income"],
        })
        assert row["eligibility_flags"] == "senior|low_income"

    def test_notes_enriched_with_contact_fields(self):
        row = listing_to_row({
            "authority": "T",
            "property_name": "P",
            "url": "",
            "phone": "408-555-0100",
            "bedrooms": "2 BR",
        })
        assert "phone: 408-555-0100" in row["notes"]
        assert "br: 2 BR" in row["notes"]

    def test_db_upsert_uses_same_path_as_normalizer(self):
        """listing_to_row output must satisfy upsert_listings required fields."""
        from housing_list_search.db import DatabaseManager
        import tempfile
        import os

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