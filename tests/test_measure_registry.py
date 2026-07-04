"""Measure registry classification and drift checks."""

from housing_list_search.dispatch import registered_handler_measures
from housing_list_search.measure_registry import (
    HANDLER_MEASURES,
    INVENTORY_MEASURES,
    KNOWN_MEASURES,
    URL_EXTRACTOR_MEASURES,
    expects_property_inventory,
    parse_target_measures,
)


def test_midpen_is_inventory():
    assert expects_property_inventory(parse_target_measures("midpen,native_requests"))


def test_housekeys_only_is_not_inventory():
    assert not expects_property_inventory(parse_target_measures("housekeys"))


def test_handler_measures_match_dispatch_registry():
    """Doctor-style drift guard: dispatch handlers must match measure_registry."""
    registered = registered_handler_measures()
    assert registered == HANDLER_MEASURES


def test_known_measures_cover_extractors_and_handlers():
    assert HANDLER_MEASURES <= KNOWN_MEASURES
    assert URL_EXTRACTOR_MEASURES <= KNOWN_MEASURES
    assert INVENTORY_MEASURES <= HANDLER_MEASURES | URL_EXTRACTOR_MEASURES
