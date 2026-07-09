"""Measure registry classification and drift checks."""

from housing_list_search.dispatch import ensure_registered, registered_handler_measures
from housing_list_search.measure_registry import (
    HANDLER_MEASURES,
    INVENTORY_MEASURES,
    KNOWN_MEASURES,
    URL_EXTRACTOR_MEASURES,
    check_handler_registration_drift,
    expects_property_inventory,
    parse_target_measures,
)


def test_midpen_is_inventory():
    assert expects_property_inventory(parse_target_measures("midpen,native_requests"))


def test_housekeys_only_is_not_inventory():
    assert not expects_property_inventory(parse_target_measures("housekeys"))


def test_handler_measures_match_dispatch_registry():
    """Doctor-style drift guard: dispatch handlers must match measure_registry."""
    ensure_registered()
    registered = registered_handler_measures()
    drift = check_handler_registration_drift(registered)
    assert drift.ok, f"missing={drift.missing} extra={drift.extra}"
    assert registered == HANDLER_MEASURES


def test_drift_detects_missing_handler():
    drift = check_handler_registration_drift(HANDLER_MEASURES - {"midpen"})
    assert not drift.ok
    assert "midpen" in drift.missing


def test_known_measures_cover_extractors_and_handlers():
    assert HANDLER_MEASURES <= KNOWN_MEASURES
    assert URL_EXTRACTOR_MEASURES <= KNOWN_MEASURES
    assert INVENTORY_MEASURES <= HANDLER_MEASURES | URL_EXTRACTOR_MEASURES
