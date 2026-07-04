"""Unit tests for CSV formula-injection guards."""

from __future__ import annotations

from housing_list_search.csv_safety import sanitize_csv_field, sanitize_csv_row


class TestCsvSafety:
    def test_plain_text_unchanged(self):
        assert sanitize_csv_field("Monroe Commons") == "Monroe Commons"

    def test_formula_prefix_escaped(self):
        assert sanitize_csv_field("=CMD|'/C calc'!A0").startswith("'")

    def test_plus_minus_at_escaped(self):
        assert sanitize_csv_field("+1234")[0] == "'"
        assert sanitize_csv_field("-1234")[0] == "'"
        assert sanitize_csv_field("@SUM(A1)")[0] == "'"

    def test_row_sanitizes_string_fields_only(self):
        row = sanitize_csv_row({"name": "=evil", "count": 3})
        assert row["name"].startswith("'")
        assert row["count"] == 3
