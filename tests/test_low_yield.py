"""Low-yield inventory gate (#789 / #1083) — lives on RunReview / needs_review."""

from housing_list_search.needs_review import find_low_yield_targets, inventory_floor_for_measures


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
    hits = find_low_yield_targets(targets, listings, [], [])
    assert hits == [("MidPen Housing", 1)]


def test_low_yield_skips_failed_and_zero():
    targets = [
        {"authority": "A", "scraping_measures": "midpen"},
        {"authority": "B", "scraping_measures": "midpen"},
    ]
    listings = {"A": [], "B": [{"property_name": "P", "source": "midpen:x", "url": "https://b/1"}]}
    hits = find_low_yield_targets(
        targets, listings, failed_targets=["B"], suspicious_zero_authorities=["A"]
    )
    assert hits == []


def test_measure_floor_catches_half_broken_portfolio(monkeypatch):
    """#1083 / #238: MidPen floor 25 flags 5 properties even when global thr is 3."""
    monkeypatch.setenv("HLS_LOW_YIELD_THRESHOLD", "3")
    assert inventory_floor_for_measures({"midpen"}) == 25
    targets = [
        {
            "authority": "MidPen Housing (Santa Clara County portfolio)",
            "scraping_measures": "midpen,native_requests",
        }
    ]
    listings = {
        "MidPen Housing (Santa Clara County portfolio)": [
            {
                "property_name": f"Prop {i}",
                "source": "midpen:find_housing",
                "url": f"https://www.midpen-housing.org/property/p{i}/",
            }
            for i in range(5)
        ]
    }
    hits = find_low_yield_targets(targets, listings, [], [])
    assert hits == [("MidPen Housing (Santa Clara County portfolio)", 5)]


def test_measure_floor_ok_when_portfolio_full(monkeypatch):
    monkeypatch.setenv("HLS_LOW_YIELD_THRESHOLD", "3")
    targets = [
        {
            "authority": "Eden Housing",
            "scraping_measures": "eden",
        }
    ]
    listings = {
        "Eden Housing": [
            {
                "property_name": f"Prop {i}",
                "source": "eden:county_list",
                "url": f"https://edenhousing.org/properties/p{i}/",
            }
            for i in range(20)  # above eden floor of 18
        ]
    }
    hits = find_low_yield_targets(targets, listings, [], [])
    assert hits == []


def test_authorities_unreliable_unions_soft_thin_signals():
    from housing_list_search.needs_review import (
        authorities_unreliable_for_disappearance,
        should_update_disappearance_baseline,
    )

    labels = authorities_unreliable_for_disappearance(
        failed_targets=["City A"],
        low_yield=[("MidPen Housing", 5)],
        suspicious_zero_authorities=["Eden Housing"],
    )
    assert "City A" in labels
    assert "MidPen Housing" in labels
    assert "Eden Housing" in labels
    assert should_update_disappearance_baseline(
        failed_targets=[], low_yield=[], suspicious_zero_authorities=[]
    )
    assert not should_update_disappearance_baseline(
        low_yield=[("MidPen Housing", 5)]
    )
