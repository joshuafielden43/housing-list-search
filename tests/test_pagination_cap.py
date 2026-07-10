"""#776: pagination safety caps must not silently truncate inventory."""

from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from housing_list_search.access import SourceFetchError


def test_source_fetch_error_pagination_cap_helper():
    err = SourceFetchError.pagination_cap(
        "midpen", max_pages=4, partial=[{"property_name": "A"}], detail="12 records"
    )
    assert isinstance(err, SourceFetchError)
    assert "max_pages=4" in str(err)
    assert "SCRAPE_FAILED" in str(err) or "truncated" in str(err).lower()
    assert len(err.partial) == 1


def test_midpen_raises_when_max_pages_full(monkeypatch):
    from housing_list_search.adapters import midpen as mp

    monkeypatch.setattr(mp, "MAX_PAGES", 2)

    def fake_get(url):
        resp = MagicMock()
        # Minimal HTML with one elementor single card
        resp.text = (
            '<div class="elementor-location-single">'
            '<a href="https://www.midpen-housing.org/property/x/">X Place</a>'
            "<p>San Jose, CA</p>"
            "<span>Wait List Open</span>"
            "</div>"
        )
        return resp

    monkeypatch.setattr(mp, "polite_get", fake_get)
    # Force every page to produce a new unique URL via parse side effect
    n = {"i": 0}

    def parse_card(card, now_iso, page_url):
        n["i"] += 1
        return {
            "authority": "MidPen Housing",
            "property_name": f"Prop {n['i']}",
            "url": f"https://midpen.example/{n['i']}",
            "address": "1 Main",
            "confidence": "high",
        }

    monkeypatch.setattr(mp, "_parse_card", parse_card)

    with pytest.raises(SourceFetchError, match="max_pages"):
        mp.scrape_midpen()


def test_midpen_ok_when_empty_page_before_cap(monkeypatch):
    from housing_list_search.adapters import midpen as mp

    monkeypatch.setattr(mp, "MAX_PAGES", 4)
    calls = {"n": 0}

    def fake_get(url):
        calls["n"] += 1
        resp = MagicMock()
        if calls["n"] == 1:
            resp.text = '<div class="elementor-location-single">x</div>'
        else:
            resp.text = "<div>no cards</div>"
        return resp

    monkeypatch.setattr(mp, "polite_get", fake_get)
    monkeypatch.setattr(
        mp,
        "_parse_card",
        lambda *a, **k: {
            "authority": "MidPen Housing",
            "property_name": "Only",
            "url": "https://midpen.example/1",
            "address": "1 Main",
            "confidence": "high",
        },
    )
    rows = mp.scrape_midpen()
    assert len(rows) == 1


def test_jsco_raises_when_max_pages_full(monkeypatch):
    from housing_list_search.adapters import john_stewart as js

    def fake_get(url):
        resp = MagicMock()
        # Always return a full page of 100 items
        items = [
            {
                "title": {"rendered": f"Building {i}"},
                "city": [list(js._JSCO_SCC_CITIES.keys())[0]],
                "link": f"https://jsco.net/p/{i}",
                "modified": "2026-01-01T00:00:00",
            }
            for i in range(100)
        ]
        resp.json.return_value = items
        return resp

    monkeypatch.setattr(js, "polite_get", fake_get)

    with pytest.raises(SourceFetchError, match="max_pages"):
        js._scrape_jsco_portfolio("https://jsco.net/properties/")
