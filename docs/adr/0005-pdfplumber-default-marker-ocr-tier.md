# Default PDF stack: pdfplumber + optional marker OCR tier

Remove PyMuPDF from the default install. Use pdfplumber for tables, text lines, and flyer heuristics. Keep marker-pdf in `requirements-ocr.txt` as last resort for scanned or scrambled PDFs.

## Context

- Repo `LICENSE` is MIT.
- PyMuPDF (`pymupdf`) is AGPL-3.0 (or commercial). Shipping it in default `requirements.txt` created a license mismatch (Vikunja **#413**).
- marker-pdf is GPL-3.0 — suitable for an opt-in OCR tier, not a MIT-default replacement.
- Gilroy and most municipal PDFs are structured tables; pdfplumber already powers the primary extraction path.

## Decision

| Tier | File | Stack |
|------|------|-------|
| **Default** | `requirements.txt` | pdfplumber only — no pymupdf |
| **Hard PDFs** | `requirements-ocr.txt` | marker-pdf after pdfplumber paths return zero |

Pipeline order in `extraction/pdf.py`: pdfplumber tables → flyer heuristics → line-regex → marker fallback.

## Consequences

- Default `pip install -r requirements.txt` is MIT-aligned for PDF parsing.
- Flyer and line paths use pdfplumber; edge-case quality may differ from PyMuPDF on some flyers — monitor via integration smoke.
- Operators who need layout/OCR recovery install `requirements-ocr.txt` on a capable host and accept GPL obligations for that tier.
- `HLS_DISABLE_MARKER_PDF=1` remains the CI/daily-cron default when marker is not installed.