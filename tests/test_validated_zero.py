"""Unit tests for Validated Zero metadata (ADR-0003)."""

from datetime import date

from housing_list_search.suspicious_zero import find_suspicious_zeros
from housing_list_search.validated_zero import (
    find_reverification_due,
    has_current_validated_zero,
    parse_validated_zero_date,
    validated_zero_status,
)


def _target(**kwargs) -> dict:
    base = {
        "authority": "City of Campbell",
        "scraping_measures": "civicplus,delegated_administrator",
        "validated_zero": "",
        "validated_zero_review_due": "",
    }
    base.update(kwargs)
    return base


class TestParseValidatedZeroDate:
    def test_iso_date(self):
        assert parse_validated_zero_date("2026-06-05") == date(2026, 6, 5)

    def test_date_with_reviewer(self):
        assert parse_validated_zero_date("2026-06-05 jcf") == date(2026, 6, 5)

    def test_invalid_cleared(self):
        assert parse_validated_zero_date("not-a-date") is None


class TestValidatedZeroStatus:
    def test_none_without_metadata(self):
        assert validated_zero_status(_target()) == "none"

    def test_current_within_review_window(self):
        target = _target(validated_zero="2026-06-05", validated_zero_review_due="2026-07-05")
        assert validated_zero_status(target, today=date(2026, 7, 4)) == "current"
        assert has_current_validated_zero(target, today=date(2026, 7, 4))

    def test_due_after_review_date(self):
        target = _target(validated_zero="2026-06-05", validated_zero_review_due="2026-07-05")
        assert validated_zero_status(target, today=date(2026, 7, 6)) == "due"
        assert not has_current_validated_zero(target, today=date(2026, 7, 6))


class TestFindReverificationDue:
    def test_finds_expired_validation(self):
        targets = [_target(validated_zero="2026-06-05", validated_zero_review_due="2026-07-05")]
        assert find_reverification_due(targets, today=date(2026, 7, 6)) == ["City of Campbell"]

    def test_omits_current_validation(self):
        targets = [_target(validated_zero="2026-06-05", validated_zero_review_due="2026-07-05")]
        assert find_reverification_due(targets, today=date(2026, 7, 4)) == []


class TestSuspiciousZeroSuppression:
    def test_current_validated_zero_suppresses_flag(self):
        targets = [_target(validated_zero="2026-06-05", validated_zero_review_due="2026-07-05")]
        assert (
            find_suspicious_zeros(
                targets,
                {"City of Campbell": []},
                [],
                today=date(2026, 7, 4),
            )
            == []
        )

    def test_expired_validation_still_flags_zero(self):
        targets = [_target(validated_zero="2026-06-05", validated_zero_review_due="2026-07-05")]
        assert find_suspicious_zeros(
            targets,
            {"City of Campbell": []},
            [],
            today=date(2026, 7, 6),
        ) == ["City of Campbell"]
