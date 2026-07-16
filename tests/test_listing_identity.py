"""Listing Identity module — keys, mirrors, alias match policy."""

from housing_list_search.listing_identity import (
    alias_matches,
    cross_source_key,
    mirror_confirm_keys,
    persistence_key,
)


def test_persistence_key_canonical_row():
    row = {
        "authority": "MidPen Housing",
        "property_name": "Oak",
        "url": "https://example.com/oak",
        "scrape_date": "2026-01-01",
    }
    assert persistence_key(row) == ("MidPen Housing", "Oak", "https://example.com/oak")


def test_mirror_confirm_keys():
    all_ids = {
        ("A", "Oak", "https://a"),
        ("B", "Oak", "https://b"),
    }
    survivors = {("A", "Oak", "https://a")}
    assert mirror_confirm_keys(all_ids, survivors) == frozenset({("B", "Oak", "https://b")})


def test_alias_matches_same_url_different_authority():
    survivor = {
        "authority": "Charities Housing",
        "property_name": "Cedar Court",
        "url": "https://example.com/cedar",
        "address": "1 Main St, San Jose, CA",
    }
    candidate = {
        "authority": "Charities Housing (Santa Clara County portfolio)",
        "property_name": "Cedar Court",
        "url": "https://example.com/cedar",
        "address": "1 Main St, San Jose, CA",
    }
    assert alias_matches(survivor, candidate) is True


def test_alias_matches_same_address_different_authority():
    survivor = {
        "authority": "City A",
        "property_name": "Park View",
        "url": "https://a/1",
        "address": "100 Oak Street, San Jose, CA 95110",
    }
    candidate = {
        "authority": "City B",
        "property_name": "Park View",
        "url": "https://b/2",
        "address": "100 Oak Street, San Jose, CA 95110",
    }
    assert alias_matches(survivor, candidate) is True


def test_alias_matches_rejects_same_authority():
    survivor = {
        "authority": "City A",
        "property_name": "Park View",
        "url": "https://a/1",
        "address": "100 Oak Street, San Jose, CA",
    }
    candidate = dict(survivor)
    assert alias_matches(survivor, candidate) is False


def test_alias_matches_rejects_short_address_only():
    survivor = {
        "authority": "City A",
        "property_name": "X",
        "url": "https://a/1",
        "address": "short",
    }
    candidate = {
        "authority": "City B",
        "property_name": "X",
        "url": "https://b/2",
        "address": "short",
    }
    assert alias_matches(survivor, candidate) is False


def test_cross_source_key_addr_surrogate():
    row = {
        "property_name": "Oak",
        "url": "hls:addr:100oakst",
        "address": "",
    }
    assert cross_source_key(row) == ("url", "hls:addr:100oakst")
