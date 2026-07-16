"""Tests for Disappearance run-diff helpers (listing identity + compute_run_diff)."""

from housing_list_search.disappearance import compute_run_diff
from housing_list_search.listing_identity import persistence_key


class TestPersistenceKey:
    def test_uses_url_when_present(self):
        item = {"authority": "City", "property_name": "Oak", "url": "https://x"}
        assert persistence_key(item) == ("City", "Oak", "https://x")

    def test_document_url_fallback(self):
        item = {"authority": "City", "property_name": "Oak", "document_url": "https://pdf"}
        assert persistence_key(item)[2] == "https://pdf"


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
