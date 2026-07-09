"""Low-yield inventory gate (#789)."""

from housing_list_search.pipeline import _find_low_yield_targets


def test_low_yield_flags_small_inventory(monkeypatch):
    monkeypatch.setenv("HLS_LOW_YIELD_THRESHOLD", "3")
    targets = [
        {
            "authority": "MidPen Housing",
            "scraping_measures": "midpen,native_requests",
        }
    ]
    listings = {
        "MidPen Housing": [
            {"property_name": "Only One", "source": "midpen:x", "url": "https://m/1"},
        ]
    }
    hits = _find_low_yield_targets(targets, listings, [], [])
    assert hits == [("MidPen Housing", 1)]


def test_low_yield_skips_failed_and_zero():
    targets = [
        {"authority": "A", "scraping_measures": "midpen"},
        {"authority": "B", "scraping_measures": "midpen"},
    ]
    listings = {"A": [], "B": [{"property_name": "P", "source": "midpen:x", "url": "https://b/1"}]}
    hits = _find_low_yield_targets(targets, listings, failed_targets=["B"], suspicious_zero_authorities=["A"])
    assert hits == []
