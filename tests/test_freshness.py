"""Tests for unified freshness / change semantics."""

from housing_list_search.freshness import (
    compute_run_diff,
    listing_identity,
    partition_removed_by_scrape_failure,
    scrape_failed_from_db_rows,
    stale_from_db_rows,
)


class TestListingIdentity:
    def test_uses_url_when_present(self):
        item = {"authority": "City", "property_name": "Oak", "url": "https://x"}
        assert listing_identity(item) == ("City", "Oak", "https://x")

    def test_document_url_fallback(self):
        item = {"authority": "City", "property_name": "Oak", "document_url": "https://pdf"}
        assert listing_identity(item)[2] == "https://pdf"


class TestComputeRunDiff:
    def test_added_and_removed(self):
        prev = [{"authority": "C", "property_name": "Old", "url": ""}]
        curr = [{"authority": "C", "property_name": "New", "url": ""}]
        diff = compute_run_diff(prev, curr)
        assert diff.removed[0][1] == "Old"
        assert diff.added[0][1] == "New"

    def test_same_name_different_url_not_deduped(self):
        prev = [{"authority": "C", "property_name": "Oak", "url": "https://a"}]
        curr = [{"authority": "C", "property_name": "Oak", "url": "https://b"}]
        diff = compute_run_diff(prev, curr)
        assert len(diff.removed) == 1
        assert len(diff.added) == 1


class TestStaleFromDbRows:
    def test_stale_excludes_already_removed(self):
        removed = {("City", "Gone", "https://g")}
        diff_rows = [
            {
                "change_type": "STALE",
                "source_authority": "City",
                "property_name": "Gone",
                "url": "https://g",
            },
            {
                "change_type": "STALE",
                "source_authority": "City",
                "property_name": "Maybe",
                "url": "",
            },
        ]
        stale = stale_from_db_rows(diff_rows, removed_keys=removed)
        assert stale[0][:2] == ("City", "Maybe")


class TestScrapeFailedSemantics:
    def test_partition_removed_by_scrape_failure(self):
        removed = [
            ("City A", "Gone", "https://a"),
            ("City B", "Lost", "https://b"),
        ]
        staff_removed, scrape_failed = partition_removed_by_scrape_failure(
            removed,
            ["City B"],
        )
        assert staff_removed == [("City A", "Gone", "https://a")]
        assert scrape_failed == [("City B", "Lost", "https://b")]

    def test_scrape_failed_from_db_rows_excludes_snapshot_keys(self):
        excluded = {("City B", "Lost", "https://b")}
        diff_rows = [
            {
                "change_type": "SCRAPE_FAILED",
                "source_authority": "City B",
                "property_name": "Lost",
                "url": "https://b",
            },
            {
                "change_type": "SCRAPE_FAILED",
                "source_authority": "City C",
                "property_name": "Only In DB",
                "url": "https://c",
            },
        ]
        keys = scrape_failed_from_db_rows(diff_rows, excluded_keys=excluded)
        assert keys == [("City C", "Only In DB", "https://c")]
