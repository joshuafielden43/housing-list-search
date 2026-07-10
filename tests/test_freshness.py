"""Tests for change helpers (compat: freshness re-exports disappearance)."""

from housing_list_search.disappearance import (
    compute_run_diff,
    listing_identity,
)
from housing_list_search.freshness import compute_run_diff as shim_compute_run_diff


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
        # shim stays wired
        assert shim_compute_run_diff(prev, curr).added == diff.added

    def test_same_name_different_url_not_deduped(self):
        prev = [{"authority": "C", "property_name": "Oak", "url": "https://a"}]
        curr = [{"authority": "C", "property_name": "Oak", "url": "https://b"}]
        diff = compute_run_diff(prev, curr)
        assert len(diff.removed) == 1
        assert len(diff.added) == 1
