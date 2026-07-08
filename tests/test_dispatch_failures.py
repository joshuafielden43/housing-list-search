"""Dispatch failure propagation when adapters error but return empty."""

from housing_list_search.dispatch import TargetContext, TargetScrapeResult, dispatch_target


def _failing_civicplus(_ctx: TargetContext) -> list[dict]:
    raise RuntimeError("[civicplus] City of Gilroy: zero records after extraction errors")


class TestDispatchFailurePropagation:
    def test_handler_exception_marks_authority_failed(self, monkeypatch):
        import housing_list_search.dispatch as dispatch

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
