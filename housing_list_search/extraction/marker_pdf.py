"""
Optional marker-pdf fallback for hard PDFs (scanned flyers, scrambled layout).

**Opt-in only (#1088 / ADR-0005):** models load only when
``HLS_ENABLE_MARKER_PDF=1`` (or true/yes). ``HLS_DISABLE_MARKER_PDF=1`` always
wins and keeps OCR dark.

**Process isolation (#1090):** conversion runs in a short-lived subprocess so
torch/Surya multi-GB RSS is reclaimed on child exit — never co-resident in the
scrape process for the rest of --run.

License (operator must accept when enabling this tier — ADR-0005 / #778):
  - marker *code*: GPL-3.0
  - model *weights*: modified AI Pubs OpenRAIL-M (Datalab) — not MIT;
    free for research/personal and startups under upstream revenue/funding
    limits; broader commercial use may need a paid Datalab license.
  - See requirements-ocr.txt and docs/adr/0005-*.md. Not legal advice.
"""

from __future__ import annotations

import logging
import os
import subprocess
import sys
import tempfile
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from housing_list_search.extraction.pdf import HousingRecord

logger = logging.getLogger(__name__)

_MARKER_CHECKED = False
_MARKER_AVAILABLE = False

# Child conversion budget (models + inference). Kill rather than hang the host.
_MARKER_SUBPROCESS_TIMEOUT_S = int(os.environ.get("HLS_MARKER_TIMEOUT_S", "600"))


def _env_truthy(name: str) -> bool:
    return os.environ.get(name, "").strip().lower() in {"1", "true", "yes"}


def marker_ocr_explicitly_enabled() -> bool:
    """True only when operator opted in AND did not force-disable."""
    if _env_truthy("HLS_DISABLE_MARKER_PDF"):
        return False
    return _env_truthy("HLS_ENABLE_MARKER_PDF")


def marker_available() -> bool:
    """Return True if OCR may run: opt-in env + package importable + not disabled.

    #1088: presence of marker-pdf alone is NOT permission (was the 9GB --run bug).
    """
    global _MARKER_CHECKED, _MARKER_AVAILABLE
    if not marker_ocr_explicitly_enabled():
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

    for rec in _extract_flyer_page(authority, document_url, 1, text):
        _add(rec)

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


def _markdown_via_subprocess(pdf_bytes: bytes, document_url: str) -> str:
    """Run marker in a child process; parent never imports torch (#1090)."""
    with tempfile.NamedTemporaryFile(suffix=".pdf", delete=False) as tmp:
        tmp.write(pdf_bytes)
        pdf_path = tmp.name
    out_path = pdf_path + ".md"
    try:
        env = os.environ.copy()
        # Child is the only place that loads models; ensure enable is set for worker.
        env["HLS_ENABLE_MARKER_PDF"] = "1"
        env.pop("HLS_DISABLE_MARKER_PDF", None)
        cmd = [
            sys.executable,
            "-m",
            "housing_list_search.extraction.marker_worker",
            pdf_path,
            out_path,
        ]
        logger.info(
            "[pdf] Spawning marker OCR subprocess for %s (timeout=%ss)",
            document_url,
            _MARKER_SUBPROCESS_TIMEOUT_S,
        )
        proc = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=_MARKER_SUBPROCESS_TIMEOUT_S,
            env=env,
            check=False,
        )
        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()[:500]
            logger.warning(
                "[pdf] Marker subprocess failed (exit %s) for %s: %s",
                proc.returncode,
                document_url,
                err or "(no stderr)",
            )
            return ""
        try:
            with open(out_path, encoding="utf-8") as f:
                return f.read()
        except OSError as exc:
            logger.warning("[pdf] Marker subprocess wrote no output for %s: %s", document_url, exc)
            return ""
    except subprocess.TimeoutExpired:
        logger.warning(
            "[pdf] Marker subprocess timed out after %ss for %s",
            _MARKER_SUBPROCESS_TIMEOUT_S,
            document_url,
        )
        return ""
    finally:
        for p in (pdf_path, out_path):
            try:
                os.unlink(p)
            except OSError:
                pass


def extract_records_via_marker(
    pdf_bytes: bytes,
    authority: str,
    document_url: str,
) -> list[HousingRecord]:
    """Run marker-pdf on PDF bytes via subprocess; return parsed HousingRecords."""
    if not marker_available():
        return []

    try:
        markdown = _markdown_via_subprocess(pdf_bytes, document_url)
    except Exception as exc:
        logger.warning("[pdf] Marker extraction failed for %s: %s", document_url, exc)
        return []

    if not markdown.strip():
        return []

    records = records_from_marker_markdown(markdown, authority, document_url)
    if records:
        logger.info(
            "[pdf] Marker extraction produced %d records from %s",
            len(records),
            document_url,
        )
    return records
