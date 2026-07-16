"""#1084: MidPen / EAH absolute URLs for stable listing_identity."""

from __future__ import annotations

from housing_list_search.adapters.eah import parse_search_results
from housing_list_search.adapters.midpen import _parse_card
from housing_list_search.listing_identity import persistence_key


def test_midpen_relative_href_becomes_absolute():
    from bs4 import BeautifulSoup

    html = """
    <div class="elementor-location-single">
      <a href="/property/oak-creek/">Oak Creek</a>
      <span>Wait List Open</span>
      <span>San Jose, CA</span>
    </div>
    """
    card = BeautifulSoup(html, "html.parser").select_one(".elementor-location-single")
    rec = _parse_card(card, "2026-07-10T00:00:00", "https://www.midpen-housing.org/find-housing/")
    assert rec is not None
    assert rec["url"] == "https://www.midpen-housing.org/property/oak-creek/"
    # Same absolute key if page already had absolute href
    html_abs = html.replace('href="/property/oak-creek/"', 'href="https://www.midpen-housing.org/property/oak-creek/"')
    card2 = BeautifulSoup(html_abs, "html.parser").select_one(".elementor-location-single")
    rec2 = _parse_card(card2, "2026-07-10T00:00:00", "https://www.midpen-housing.org/find-housing/2/")
    assert persistence_key(rec) == persistence_key(rec2)


def test_eah_relative_href_becomes_absolute():
    html = """
    <ul>
      <li>
        <h2><a href="/apartments/oak-creek/">Oak Creek</a></h2>
        <p>100 Oak St, San Jose, CA 95110</p>
      </li>
    </ul>
    """
    recs = parse_search_results(
        html,
        "2026-07-10T00:00:00",
        "https://www.eahhousing.org/apartment-search-result-never-delete/",
    )
    assert len(recs) == 1
    assert recs[0]["url"] == "https://www.eahhousing.org/apartments/oak-creek/"
