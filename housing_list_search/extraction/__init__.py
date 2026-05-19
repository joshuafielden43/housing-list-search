"""
Extraction package public API.

Provides a unified way to turn a target (URL or known portal) into
a list of structured HousingRecord objects.

This is the integration point for the high-quality per-city extractors
(pdf tables, San José Next.js JSON API, future ones).
"""

from __future__ import annotations

from typing import List

from .pdf import HousingRecord, extract_records_from_pdf
from .san_jose import extract_san_jose_listings


def extract_target(url: str, authority: str = "") -> List[HousingRecord]:
    """
    Main dispatcher.

    Given a URL (and optional authority label from TARGETS.md),
    returns a list of high-quality HousingRecord objects.

    Currently knows:
    - City of San José portal → dedicated rich JSON extractor
    - Any .pdf or Gilroy DocumentCenter/View links → table-aware PDF extractor
    - Future: more city-specific extractors will be added here.
    """
    u = (url or "").lower()
    auth = (authority or "").lower()

    # San José dedicated path (the modern portal we reverse-engineered)
    if "sanjoseca.gov" in u or "san jose" in auth or "sanjosé" in auth:
        return extract_san_jose_listings()

    # Direct PDF or Gilroy DocumentCenter links
    if u.endswith(".pdf") or "documentcenter/view" in u or "documentcenter" in u:
        auth_label = authority or "City of Gilroy"
        return extract_records_from_pdf(url, authority=auth_label)

    # No high-quality extractor known for this target yet.
    # (Generic scraping is deliberately not here — it was too noisy.)
    return []


__all__ = [
    "HousingRecord",
    "extract_target",
    "extract_records_from_pdf",
    "extract_san_jose_listings",
]
