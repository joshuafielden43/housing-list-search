"""Unit tests for marker-pdf fallback (no model load)."""

from unittest.mock import patch

import pytest

from housing_list_search.extraction.marker_pdf import (
    marker_available,
    marker_ocr_explicitly_enabled,
    records_from_marker_markdown,
)


def _reset_marker_cache() -> None:
    import housing_list_search.extraction.marker_pdf as mp

    mp._MARKER_CHECKED = False
    mp._MARKER_AVAILABLE = False


class TestMarkerAvailable:
    def test_disabled_by_env(self, monkeypatch):
        monkeypatch.setenv("HLS_DISABLE_MARKER_PDF", "1")
        monkeypatch.setenv("HLS_ENABLE_MARKER_PDF", "1")
        _reset_marker_cache()
        assert marker_ocr_explicitly_enabled() is False
        assert marker_available() is False

    def test_not_enabled_by_default(self, monkeypatch):
        """#1088: package presence alone must not enable OCR."""
        monkeypatch.delenv("HLS_DISABLE_MARKER_PDF", raising=False)
        monkeypatch.delenv("HLS_ENABLE_MARKER_PDF", raising=False)
        _reset_marker_cache()
        assert marker_ocr_explicitly_enabled() is False
        assert marker_available() is False

    def test_available_when_opted_in_and_installed(self, monkeypatch):
        """Skipped on CI when marker-pdf not installed."""
        pytest.importorskip("marker.converters.pdf")
        monkeypatch.delenv("HLS_DISABLE_MARKER_PDF", raising=False)
        monkeypatch.setenv("HLS_ENABLE_MARKER_PDF", "1")
        _reset_marker_cache()
        assert marker_available() is True


class TestRecordsFromMarkerMarkdown:
    def test_parses_markdown_table_row_with_address(self):
        text = """
| Property | Address | Phone |
| Oak Manor | 123 Main St, Gilroy, CA 95020 | (408) 555-0100 |
"""
        records = records_from_marker_markdown(text, "City of Gilroy", "https://example.com/a.pdf")
        assert len(records) >= 1
        assert any(
            "Oak" in (r.property_name or "") or "123 Main" in (r.address or "") for r in records
        )

    def test_flyer_style_markdown(self):
        text = """
Wheeler Manor
Apartments
651 W. 6th St
Gilroy, CA 95020
(408) 847-5490
Available Now!!!
1 Bedroom - 1 Bath
Rent $1,822.00
"""
        records = records_from_marker_markdown(
            text, "City of Gilroy", "https://example.com/flyer.pdf"
        )
        assert len(records) >= 1
        assert records[0].property_name


class TestExtractRecordsFromPdfMarkerFallback:
    def test_marker_not_called_without_opt_in(self, monkeypatch):
        """#1088: empty prior paths must not invoke marker without HLS_ENABLE_MARKER_PDF."""
        monkeypatch.delenv("HLS_ENABLE_MARKER_PDF", raising=False)
        monkeypatch.delenv("HLS_DISABLE_MARKER_PDF", raising=False)
        from housing_list_search.extraction.pdf import extract_records_from_pdf

        with (
            patch("housing_list_search.extraction.pdf._fetch_pdf", return_value=b"%PDF-1.4"),
            patch(
                "housing_list_search.extraction.pdf.extract_records_from_pdf_bytes",
                return_value=[],
            ),
            patch(
                "housing_list_search.extraction.pdf._extract_flyer_pages_from_pdf", return_value=[]
            ),
            patch(
                "housing_list_search.extraction.pdf.extract_text_lines_from_pdf", return_value=[]
            ),
            patch(
                "housing_list_search.extraction.marker_pdf.extract_records_via_marker"
            ) as mock_marker,
        ):
            result = extract_records_from_pdf("https://example.com/x.pdf", "Test")
            mock_marker.assert_not_called()
            assert result == []

    def test_marker_called_when_opted_in(self, monkeypatch):
        monkeypatch.setenv("HLS_ENABLE_MARKER_PDF", "1")
        monkeypatch.delenv("HLS_DISABLE_MARKER_PDF", raising=False)
        from housing_list_search.extraction.pdf import extract_records_from_pdf

        with (
            patch("housing_list_search.extraction.pdf._fetch_pdf", return_value=b"%PDF-1.4"),
            patch(
                "housing_list_search.extraction.pdf.extract_records_from_pdf_bytes",
                return_value=[],
            ),
            patch(
                "housing_list_search.extraction.pdf._extract_flyer_pages_from_pdf", return_value=[]
            ),
            patch(
                "housing_list_search.extraction.pdf.extract_text_lines_from_pdf", return_value=[]
            ),
            patch(
                "housing_list_search.extraction.marker_pdf.marker_available",
                return_value=True,
            ),
            patch(
                "housing_list_search.extraction.marker_pdf.extract_records_via_marker"
            ) as mock_marker,
        ):
            mock_marker.return_value = []
            result = extract_records_from_pdf("https://example.com/x.pdf", "Test")
            mock_marker.assert_called_once()
            assert result == []
