"""
Extraction package public API.

High-quality per-platform extractors (Bloom Housing, PDF tables, …).
URL dispatch is registered in housing_list_search.dispatch.
"""

from __future__ import annotations

from .bloom_housing import (
    BLOOM_DOMAINS,
    extract_bloom_for_target,
    extract_bloom_housing_listings,
    extract_san_jose_listings,
    is_bloom_url,
)
from .pdf import HousingRecord, extract_records_from_pdf


def extract_target(url: str, authority: str = "") -> list[HousingRecord]:
    """
    Standalone URL extraction entry point (integration tests, ground_truth).
    Delegates to the dispatch registry with measure gate disabled.
    """
    from housing_list_search.dispatch import extract_target as _dispatch_extract

    return _dispatch_extract(url, authority)


__all__ = [
    "BLOOM_DOMAINS",
    "HousingRecord",
    "extract_target",
    "extract_records_from_pdf",
    "extract_bloom_housing_listings",
    "extract_bloom_for_target",
    "extract_san_jose_listings",
    "is_bloom_url",
]
