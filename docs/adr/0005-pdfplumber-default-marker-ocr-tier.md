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

## OCR tier license obligations (#778)

Installing `requirements-ocr.txt` is **not** covered by this repo’s MIT alone. Operators accept **two** third-party regimes (verify upstream if versions change):

| Artifact | License (marker / Datalab, as of 2025–2026) | Operator implication |
|----------|---------------------------------------------|----------------------|
| **marker-pdf / Marker code** | **GPL-3.0** (or later) | Copyleft: distributing a modified binary that includes this code may obligate you to offer corresponding source under GPL. Local private operator use on a single host is the intended path for this project. |
| **Model weights** (Marker pipeline / related Datalab models) | **Modified AI Pubs OpenRAIL-M** (see [datalab-to/marker](https://github.com/datalab-to/marker) `MODEL_LICENSE` and README “Commercial usage”) | Weights are **not** MIT/Apache. Free for research, personal use, and startups under the revenue/funding thresholds stated upstream (historically on the order of **$2M** funding/revenue — **read the current `MODEL_LICENSE` before relying on the number**). Broader commercial use may require a paid license from Datalab. Use restrictions typical of OpenRAIL-M (no prohibited uses listed in that license) also apply. |

**Default daily posture for housing-list-search:** do **not** install the OCR tier on the cron host. Prefer pdfplumber only; set `HLS_DISABLE_MARKER_PDF=1` when marker is present but must stay dark. Enable OCR only in a dedicated venv on a capable machine after accepting GPL + weight terms.

This is **not legal advice**. Re-check [github.com/datalab-to/marker](https://github.com/datalab-to/marker) `LICENSE` and `MODEL_LICENSE` when upgrading `marker-pdf` pins.

## Consequences

- Default `pip install -r requirements.txt` is MIT-aligned for PDF parsing.
- Flyer and line paths use pdfplumber; edge-case quality may differ from PyMuPDF on some flyers — monitor via integration smoke.
- Operators who need layout/OCR recovery install `requirements-ocr.txt` on a capable host and accept **GPL code + OpenRAIL-M weight** obligations for that tier (#778).
- `HLS_DISABLE_MARKER_PDF=1` remains the CI/daily-cron default when marker is not installed.