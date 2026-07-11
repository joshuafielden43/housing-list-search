"""BMR multi-property contact-directory PDF text (Los Altos View/2785 style)."""

from housing_list_search.extraction.pdf import (
    _looks_like_bmr_contact_directory,
    extract_bmr_contact_directory,
)

# Exact pdfplumber line-join shape from live View/2785 (2026-07-10 probe).
LOS_ALTOS_2785_TEXT = """\
LOS ALTOS BMR RENTAL PROGRAM
The City of Los Altos’ Below Market Rate (BMR) Housing Rental Program helps provide rental
apartments for low and moderate-income households. BMR renters must meet special income and
other eligibility requirements.
The individual on-site property managers manage waitlists and provide application forms for
available BMR rental units. Housing Group, the City’s BMR Housing Program Administrator,
confirms household income and eligibility requirements before leases are signed.
Please contact property managers about BMR rental availability and to be placed on the
individual property’s wait list.
Los Altos Gardens Fremont Avenue
960 San Antonio Road, Los Altos CA 94022 919 Fremont Ave, Los Altos, CA 94024
(650) 209-9746 (408) 268-0899
www.losaltosgardens@deanzaproperties.com
The Terraces 569 Lassen Street
373 Pine Lane, Los Altos, CA 94022 569 Lassen Street, Los Altos, CA 94022
(650) 948-8291 408-425-3036
debbie.duenas-fears@humangood.org farhoudi@me.com
Colonnade Apartments
4750 El Camino Real, Los Altos, CA 94022
(650) 559-8500 I www.colonnadeapartments.stanford.edu
COMING SOON: MiLa Project, 330 Distel Circle, Los Altos CA 94022
"""


def test_detects_los_altos_style_directory():
    assert _looks_like_bmr_contact_directory(LOS_ALTOS_2785_TEXT) is True
    assert _looks_like_bmr_contact_directory("Random flyer with one phone (650) 555-1212") is False


def test_extracts_five_properties_plus_coming_soon():
    recs = extract_bmr_contact_directory(
        LOS_ALTOS_2785_TEXT,
        authority="City of Los Altos",
        document_url="https://www.losaltosca.gov/DocumentCenter/View/2785",
    )
    names = [r.property_name for r in recs]
    assert "Los Altos Gardens" in names
    assert "Fremont Avenue" in names
    assert "The Terraces" in names
    assert "569 Lassen Street" in names
    assert "Colonnade Apartments" in names
    assert "MiLa Project" in names
    assert len(recs) >= 6

    gardens = next(r for r in recs if r.property_name == "Los Altos Gardens")
    assert "960 San Antonio" in gardens.address
    assert "650" in gardens.phone.replace(" ", "")
    assert gardens.listing_status == "waitlist"
    assert "waitlist" in gardens.notes.lower()
    # Distinct per-property identity URL (not all sharing bare View/2785) (#1108)
    assert gardens.url.endswith("#los-altos-gardens") or "#los-altos-gardens" in gardens.url
    urls = {r.url for r in recs}
    assert len(urls) == len(recs)

    mila = next(r for r in recs if "MiLa" in r.property_name)
    assert mila.listing_status == "coming_soon"
    assert "330 Distel" in mila.address


def test_funding_list_not_directory():
    text = """
    City of Los Altos Affordable Housing Development Funding Sources List
    State HCD SuperNOFA and AHSC programs.
    """
    assert extract_bmr_contact_directory(text, authority="City of Los Altos") == []
