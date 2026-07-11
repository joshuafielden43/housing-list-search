"""Dispatch failure propagation when adapters error but return empty."""

from housing_list_search.access import SourceFetchError
from housing_list_search.dispatch import TargetContext, TargetScrapeResult, dispatch_target


def _failing_civicplus(_ctx: TargetContext) -> list[dict]:
    raise RuntimeError("[civicplus] City of Gilroy: zero records after extraction errors")


def _fetch_fail_midpen(_ctx: TargetContext) -> list[dict]:
    raise SourceFetchError("midpen: fetch failed for https://www.midpen-housing.org/…")


def _partial_then_fail(_ctx: TargetContext) -> list[dict]:
    raise SourceFetchError(
        "midpen: page 2 failed",
        partial=[
            {
                "authority": "MidPen Housing (Santa Clara County portfolio)",
                "property_name": "Partial Prop",
                "url": "https://example.com/p1",
            }
        ],
    )


class TestDispatchFailurePropagation:
    def test_ensure_registered_is_idempotent(self):
        """#1054: second ensure_registered must not re-bind handlers (mock-safe)."""
        import housing_list_search.dispatch as dispatch

        dispatch.ensure_registered()
        before = dispatch._MEASURE_HANDLERS.get("civicplus")
        dispatch.ensure_registered()
        assert dispatch._MEASURE_HANDLERS.get("civicplus") is before

    def test_handler_exception_marks_authority_failed(self, monkeypatch):
        import housing_list_search.dispatch as dispatch

        dispatch.ensure_registered()
        monkeypatch.setitem(dispatch._MEASURE_HANDLERS, "civicplus", _failing_civicplus)

        ctx = TargetContext(
            authority="City of Gilroy",
            url="https://www.cityofgilroy.org/housing",
            measures={"civicplus"},
        )
        outcome: TargetScrapeResult = dispatch_target(ctx)

        assert outcome.authority == "City of Gilroy"
        assert outcome.records == []
        assert outcome.had_error is True

    def test_source_fetch_error_marks_had_error(self, monkeypatch):
        """#1048: polite_get failure must not look like empty inventory success."""
        import housing_list_search.dispatch as dispatch

        dispatch.ensure_registered()
        monkeypatch.setitem(dispatch._MEASURE_HANDLERS, "midpen", _fetch_fail_midpen)

        ctx = TargetContext(
            authority="MidPen Housing (Santa Clara County portfolio)",
            url="https://www.midpen-housing.org/find-housing/",
            measures={"midpen"},
        )
        outcome = dispatch_target(ctx)
        assert outcome.had_error is True
        assert outcome.records == []

    def test_source_fetch_error_preserves_partial_records(self, monkeypatch):
        """Partial pages + had_error=True (upsert what we can, still SCRAPE_FAILED)."""
        import housing_list_search.dispatch as dispatch

        dispatch.ensure_registered()
        monkeypatch.setitem(dispatch._MEASURE_HANDLERS, "midpen", _partial_then_fail)

        ctx = TargetContext(
            authority="MidPen Housing (Santa Clara County portfolio)",
            url="https://www.midpen-housing.org/find-housing/",
            measures={"midpen"},
        )
        outcome = dispatch_target(ctx)
        assert outcome.had_error is True
        assert len(outcome.records) == 1
        assert outcome.records[0]["property_name"] == "Partial Prop"
