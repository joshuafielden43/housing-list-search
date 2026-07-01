"""Tests for unified freshness / change semantics."""

from housing_list_search.freshness import (
    compute_run_diff,
    listing_identity,
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
        assert ("C", "Old", "") in diff.removed
        assert ("C", "New", "") in diff.added

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
            {"change_type": "STALE", "source_authority": "City", "property_name": "Gone", "url": "https://g"},
            {"change_type": "STALE", "source_authority": "City", "property_name": "Maybe", "url": ""},
        ]
        stale = stale_from_db_rows(diff_rows, removed_keys=removed)
        assert stale == [("City", "Maybe", "")]