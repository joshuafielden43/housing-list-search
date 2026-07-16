"""#1107 / #244: staff summary honesty — open units vs waitlist enrollment; contacts."""

from housing_list_search.outputs import (
    _contact_lines,
    _listing_is_open,
    _listing_is_waitlist_enrolling,
    generate_daily_summary,
)
from housing_list_search.status_labels import resolve_status_label


def test_vendor_waitlist_open_is_enrollment_not_unit_open():
    """#244: Waitlist Open ≠ vacant unit available today."""
    listing = {
        "property_name": "Oak Manor",
        "status": "Waitlist Open",
        "listing_status": "",
        "notes": "",
    }
    assert _listing_is_open(listing) is False
    assert _listing_is_waitlist_enrolling(listing) is True


def test_los_altos_contact_directory_not_open():
    listing = {
        "property_name": "Los Altos Gardens",
        "listing_status": "waitlist",
        "status": resolve_status_label({"listing_status": "waitlist"}),
        "notes": "BMR rental contact directory; waitlist via on-site property manager",
    }
    assert listing["status"] == "Waitlist"
    assert _listing_is_open(listing) is False
    assert _listing_is_waitlist_enrolling(listing) is False


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


def test_contact_lines_from_fields():
    lines = _contact_lines(
        {
            "phone": "(408) 555-0100",
            "email": "leasing@example.org",
            "administrator": "Alta Housing",
            "administrator_phone": "(650) 555-0199",
        }
    )
    assert any("408" in ln for ln in lines)
    assert any("leasing@example.org" in ln for ln in lines)
    assert any("Alta Housing" in ln for ln in lines)


def test_daily_summary_splits_open_and_waitlist(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    listings = [
        {
            "property_name": "Open Arms Apts",
            "authority": "City A",
            "status": "Open",
            "listing_status": "open",
            "source": "bloom:x",
            "phone": "408-555-1111",
            "url": "https://example.com/open",
            "notes": "",
        },
        {
            "property_name": "Waitlist Towers",
            "authority": "City A",
            "status": "Waitlist Open",
            "listing_status": "waitlist",
            "source": "midpen:x",
            "email": "apply@waitlist.example",
            "url": "https://example.com/wait",
            "notes": "",
        },
    ]
    generate_daily_summary(listings)
    md = (tmp_path / "daily_summary.md").read_text(encoding="utf-8")
    assert "CURRENTLY OPEN" in md
    assert "Open Arms Apts" in md
    assert "Phone: 408-555-1111" in md
    assert "WAITLISTS ACCEPTING ENROLLMENT" in md
    assert "Waitlist Towers" in md
    assert "WAITLISTS ACCEPTING ENROLLMENT" in md
    assert "vacant unit" in md
    assert "Email: apply@waitlist.example" in md
    assert "1 open / accepting applications (units)" in md
    assert "1 waitlist(s) accepting enrollment" in md
