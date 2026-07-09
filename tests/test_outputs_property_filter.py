"""Staff summary excludes portal/program from open list (#989)."""

from housing_list_search.outputs import _listing_is_summary_candidate


def test_portal_not_summary_candidate():
    assert (
        _listing_is_summary_candidate(
            {
                "property_name": "HouseKeys Registration",
                "source": "housekeys:morgan_hill",
                "url": "https://housekeys1.com/",
            }
        )
        is False
    )


def test_property_can_be_summary_candidate():
    assert (
        _listing_is_summary_candidate(
            {
                "property_name": "Oak Manor Apartments",
                "source": "midpen:find_housing",
                "url": "https://midpen.example/p1",
                "address": "1 Main St",
            }
        )
        is True
    )
