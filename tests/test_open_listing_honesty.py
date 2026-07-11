"""#1107: open/accepting summary must not treat contact-directory waitlists as open."""

from housing_list_search.outputs import _listing_is_open
from housing_list_search.status_labels import resolve_status_label


def test_vendor_waitlist_open_still_counts():
    assert (
        _listing_is_open(
            {
                "property_name": "Oak Manor",
                "status": "Waitlist Open",
                "listing_status": "",
                "notes": "",
            }
        )
        is True
    )


def test_los_altos_contact_directory_not_open():
    listing = {
        "property_name": "Los Altos Gardens",
        "listing_status": "waitlist",
        "status": resolve_status_label({"listing_status": "waitlist"}),
        "notes": "BMR rental contact directory; waitlist via on-site property manager",
    }
    assert listing["status"] == "Waitlist"
    assert _listing_is_open(listing) is False


def test_coming_soon_not_open():
    assert (
        _listing_is_open(
            {
                "property_name": "MiLa Project",
                "listing_status": "coming_soon",
                "status": "Coming Soon",
                "notes": "COMING SOON (BMR rental flyer)",
            }
        )
        is False
    )


def test_accepting_applications_is_open():
    assert (
        _listing_is_open(
            {
                "property_name": "Foo",
                "listing_status": "",
                "status": "Check with property",
                "notes": "Accepting Applications — 1BR",
            }
        )
        is True
    )
