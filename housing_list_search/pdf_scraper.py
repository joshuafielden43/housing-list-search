"""
Deprecated — use housing_list_search.extraction.pdf instead.

Thin shim retained so any external imports of extract_from_pdf keep working.
"""

from __future__ import annotations

import warnings

from housing_list_search.extraction.pdf import HousingRecord, extract_records_from_pdf

__all__ = ["HousingRecord", "extract_from_pdf"]


def extract_from_pdf(pdf_url: str, authority: str) -> list[HousingRecord]:
    warnings.warn(
        "pdf_scraper.extract_from_pdf is deprecated; use extraction.pdf.extract_records_from_pdf",
        DeprecationWarning,
        stacklevel=2,
    )
    return extract_records_from_pdf(pdf_url, authority=authority)
