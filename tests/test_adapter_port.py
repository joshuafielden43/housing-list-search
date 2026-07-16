"""Adapter port: every registered measure Handler is run(TargetContext)."""

from housing_list_search.dispatch import (
    _HANDLER_SPECS,
    _register_measure_handlers,
    _reset_registration_for_tests,
    registered_handler_measures,
)
from housing_list_search.measure_registry import HANDLER_MEASURES
from housing_list_search.target_context import TargetContext


def test_handler_specs_cover_registry_handler_measures():
    specs = {m for m, _, _ in _HANDLER_SPECS}
    assert specs == set(HANDLER_MEASURES)


def test_all_handlers_are_run_callables():
    _reset_registration_for_tests()
    import housing_list_search.dispatch as d

    d._MEASURE_HANDLERS.clear()
    _register_measure_handlers()
    registered = registered_handler_measures()
    assert registered == HANDLER_MEASURES
    for measure in HANDLER_MEASURES:
        handler = d._MEASURE_HANDLERS[measure]
        assert handler.__name__ == "run", measure
        assert callable(handler)
        assert handler.__code__.co_argcount >= 1


def test_midpen_run_delegates_to_scrape(monkeypatch):
    from housing_list_search.adapters import midpen

    called: list[tuple] = []

    def fake_scrape(authority="", url=""):
        called.append((authority, url))
        return [{"property_name": "X", "authority": authority, "url": url}]

    monkeypatch.setattr(midpen, "scrape_midpen", fake_scrape)
    out = midpen.run(TargetContext(authority="MidPen Housing", url="https://midpen.example/"))
    assert called == [("MidPen Housing", "https://midpen.example/")]
    assert out[0]["property_name"] == "X"
