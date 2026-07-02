"""
Optional marker-pdf fallback for hard PDFs (scanned flyers, scrambled layout).

Loaded lazily — torch/model init is expensive (~30–60s first call).
Disabled when HLS_DISABLE_MARKER_PDF=1 is set.
"""

from __future__ import annotations

import io
import logging
import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from housing_list_search.extraction.pdf import HousingRecord

logger = logging.getLogger(__name__)

_CONVERTER = None
_MARKER_CHECKED = False
_MARKER_AVAILABLE = False


def marker_available() -> bool:
    """Return True if marker-pdf is installed and not explicitly disabled."""
    global _MARKER_CHECKED, _MARKER_AVAILABLE
    if os.environ.get("HLS_DISABLE_MARKER_PDF", "").strip() in {"1", "true", "yes"}:
        return False
    if _MARKER_CHECKED:
        return _MARKER_AVAILABLE
    _MARKER_CHECKED = True
    try:
        import marker.converters.pdf  # noqa: F401

        _MARKER_AVAILABLE = True
    except ImportError:
        _MARKER_AVAILABLE = False
    return _MARKER_AVAILABLE


def _get_converter():
    global _CONVERTER
    if _CONVERTER is not None:
        return _CONVERTER
    from marker.converters.pdf import PdfConverter
    from marker.models import create_model_dict

    logger.info("[pdf] Loading marker-pdf models (first use may take ~30–60s)")
    _CONVERTER = PdfConverter(artifact_dict=create_model_dict())
    return _CONVERTER


def records_from_marker_markdown(
    text: str,
    authority: str,
    document_url: str,
) -> list[HousingRecord]:
    """Parse marker markdown into HousingRecords using existing heuristics."""
    from housing_list_search.extraction.pdf import (
        _extract_flyer_page,
        parse_housing_line,
    )

    if not text or not text.strip():
        return []

    records: list[HousingRecord] = []
    seen: set[tuple[str, str]] = set()

    def _add(rec: HousingRecord | None) -> None:
        if not rec:
            return
        rec.authority = authority
        key = (rec.property_name or "", rec.address or rec.raw_line[:80])
        if key in seen:
            return
        seen.add(key)
        records.append(rec)

    # Whole-document flyer pass
    for rec in _extract_flyer_page(authority, document_url, 1, text):
        _add(rec)

    # Per-page chunks (marker paginate_output uses repeated dash lines)
    page_chunks = _split_marker_pages(text)
    for page_number, chunk in enumerate(page_chunks, start=1):
        for rec in _extract_flyer_page(authority, document_url, page_number, chunk):
            _add(rec)

    for page_number, line in _iter_marker_lines(text):
        rec = parse_housing_line(line, document_url=document_url, page_number=page_number)
        _add(rec)

    return records


def _split_marker_pages(text: str) -> list[str]:
    """Split marker markdown on page separator lines (48 dashes)."""
    import re

    parts = re.split(r"\n-{48,}\n", text)
    return [p.strip() for p in parts if p.strip()]


def _iter_marker_lines(text: str):
    """Yield (page_number, flattened_line) from marker markdown."""
    page_number = 1
    for chunk in _split_marker_pages(text) or [text]:
        for raw in chunk.splitlines():
            line = raw.strip()
            if not line or line.startswith("#"):
                continue
            if line.startswith("|") and "|" in line[1:]:
                cells = [
                    c.strip()
                    for c in line.split("|")
                    if c.strip() and not set(c.strip()) <= set("-:")
                ]
                if not cells:
                    continue
                line = " | ".join(cells)
            elif line.startswith(("-", "*", ">")):
                line = line.lstrip("-*#> ").strip()
            if len(line) < 15:
                continue
            yield page_number, line
        page_number += 1


def extract_records_via_marker(
    pdf_bytes: bytes,
    authority: str,
    document_url: str,
) -> list[HousingRecord]:
    """Run marker-pdf on in-memory PDF bytes; return parsed HousingRecords."""
    if not marker_available():
        return []

    try:
        converter = _get_converter()
        rendered = converter(io.BytesIO(pdf_bytes))
        markdown = getattr(rendered, "markdown", "") or ""
    except Exception as exc:
        logger.warning("[pdf] Marker extraction failed for %s: %s", document_url, exc)
        return []

    records = records_from_marker_markdown(markdown, authority, document_url)
    if records:
        logger.info(
            "[pdf] Marker extraction produced %d records from %s",
            len(records),
            document_url,
        )
    return records
