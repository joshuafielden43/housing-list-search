"""Measure registry classification."""

from housing_list_search.measure_registry import expects_property_inventory, parse_target_measures


def test_midpen_is_inventory():
    assert expects_property_inventory(parse_target_measures("midpen,native_requests"))


def test_housekeys_only_is_not_inventory():
    assert not expects_property_inventory(parse_target_measures("housekeys"))
