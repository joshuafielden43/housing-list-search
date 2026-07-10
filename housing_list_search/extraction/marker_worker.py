"""
Short-lived marker OCR worker process (#1090).

Invoked as: python -m housing_list_search.extraction.marker_worker <pdf_path> <out_md_path>

Loads torch/models only in this process; parent scrape process never imports marker.
Exit 0 with markdown written; non-zero on failure.
"""

from __future__ import annotations

import logging
import sys

logging.basicConfig(level=logging.INFO, format="[marker_worker] %(message)s")
logger = logging.getLogger("marker_worker")


def main(argv: list[str] | None = None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) != 2:
        logger.error("usage: marker_worker <pdf_path> <out_md_path>")
        return 2
    pdf_path, out_path = args
    try:
        from marker.converters.pdf import PdfConverter
        from marker.models import create_model_dict
    except ImportError as exc:
        logger.error("marker-pdf not installed: %s", exc)
        return 1

    try:
        logger.info("Loading marker models (this process only; exits when done)")
        converter = PdfConverter(artifact_dict=create_model_dict())
        rendered = converter(pdf_path)
        markdown = getattr(rendered, "markdown", "") or ""
        with open(out_path, "w", encoding="utf-8") as f:
            f.write(markdown)
        logger.info("Wrote %d chars of markdown to %s", len(markdown), out_path)
        return 0
    except Exception as exc:
        logger.error("conversion failed: %s", exc)
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
