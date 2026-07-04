"""Unit tests for suspicious zero detection."""

from housing_list_search.suspicious_zero import (
    expects_property_inventory,
    find_suspicious_zeros,
    parse_target_measures,
    property_inventory_count,
)


def _target(authority: str, measures: str) -> dict:
    return {"authority": authority, "scraping_measures": measures}


def _property(authority: str = "City A", **kwargs) -> dict:
    return {
        "authority": authority,
        "property_name": kwargs.get("property_name", "Oak Homes"),
        "address": kwargs.get("address", "100 Oak St"),
        "source": kwargs.get("source", "bloom:test"),
    }


def _portal(authority: str = "City A") -> dict:
    return {
        "authority": authority,
        "property_name": "Register via HouseKeys",
        "source": "housekeys:register",
        "administrator": "HouseKeys",
        "status": "registration required",
        "notes": "Register at housekeys1.com",
    }


class TestParseTargetMeasures:
    def test_normalizes_aliases_and_case(self):
        assert parse_target_measures("CDN, Native_Requests") == {"civicplus", "native_requests"}


class TestExpectsPropertyInventory:
    def test_vendor_portfolio(self):
        assert expects_property_inventory(parse_target_measures("midpen,native_requests"))

    def test_housekeys_only_is_portal(self):
        assert not expects_property_inventory(parse_target_measures("housekeys"))

    def test_milpitas_measures_are_portal_only(self):
        measures = parse_target_measures("delegated_administrator,housekeys,notification_based")
        assert not expects_property_inventory(measures)

    def test_gilroy_has_inventory_via_civicplus(self):
        assert expects_property_inventory(parse_target_measures("housekeys,civicplus"))

    def test_no_public_list_skipped(self):
        assert not expects_property_inventory(parse_target_measures("no_public_list"))

    def test_waf_blocked_skipped(self):
        assert not expects_property_inventory(parse_target_measures("gis,waf_blocked"))


class TestPropertyInventoryCount:
    def test_counts_property_not_portal(self):
        listings = [_property(), _portal(), _property(property_name="Pine Court")]
        assert property_inventory_count(listings) == 2


class TestFindSuspiciousZeros:
    def test_flags_zero_property_inventory(self):
        targets = [_target("MidPen Housing", "midpen,native_requests")]
        listings_by_authority = {"MidPen Housing": []}
        assert find_suspicious_zeros(targets, listings_by_authority, []) == ["MidPen Housing"]

    def test_ignores_portal_only_zero(self):
        targets = [_target("City of Morgan Hill", "housekeys")]
        listings_by_authority = {"City of Morgan Hill": [_portal("City of Morgan Hill")]}
        assert find_suspicious_zeros(targets, listings_by_authority, []) == []

    def test_ignores_failed_authorities(self):
        targets = [_target("MidPen Housing", "midpen,native_requests")]
        assert find_suspicious_zeros(targets, {}, ["MidPen Housing"]) == []

    def test_not_flagged_when_properties_present(self):
        targets = [_target("Eden Housing", "eden,native_requests")]
        listings_by_authority = {"Eden Housing": [_property("Eden Housing")]}
        assert find_suspicious_zeros(targets, listings_by_authority, []) == []

    def test_flags_when_only_portal_on_inventory_target(self):
        targets = [_target("City of Gilroy", "housekeys,civicplus")]
        listings_by_authority = {"City of Gilroy": [_portal("City of Gilroy")]}
        assert find_suspicious_zeros(targets, listings_by_authority, []) == ["City of Gilroy"]
