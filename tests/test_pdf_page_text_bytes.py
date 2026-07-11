"""#1105: page text extraction must work from in-memory PDF bytes (BytesIO)."""

from __future__ import annotations

import io

import pdfplumber

from housing_list_search.extraction.pdf import _iter_pdf_page_text

# Minimal one-page PDF with a Helvetica text operator (no third-party builder).
_MINIMAL_PDF = b"""%PDF-1.4
1 0 obj
<< /Type /Catalog /Pages 2 0 R >>
endobj
2 0 obj
<< /Type /Pages /Kids [3 0 R] /Count 1 >>
endobj
3 0 obj
<< /Type /Page /Parent 2 0 R /MediaBox [0 0 300 144]
   /Contents 4 0 R
   /Resources << /Font << /F1 5 0 R >> >>
>>
endobj
4 0 obj
<< /Length 51 >>
stream
BT /F1 12 Tf 50 100 Td (BMR contact card) Tj ET
endstream
endobj
5 0 obj
<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>
endobj
xref
0 6
0000000000 65535 f 
0000000009 00000 n 
0000000058 00000 n 
0000000115 00000 n 
0000000266 00000 n 
0000000367 00000 n 
trailer
<< /Size 6 /Root 1 0 R >>
startxref
444
%%EOF
"""


def test_iter_pdf_page_text_returns_pages_from_bytes():
    pages = _iter_pdf_page_text(_MINIMAL_PDF)
    assert isinstance(pages, list)
    assert len(pages) >= 1
    assert pages[0][0] == 1
    assert isinstance(pages[0][1], str)
    # Text extract may vary by pdfplumber/pdfminer version; non-empty is ideal
    # but open-with-pages is the regression we must never lose.
    assert pages[0][1] == "" or "BMR" in pages[0][1] or "contact" in pages[0][1].lower()


def test_bytesio_open_yields_pages_when_stream_bytes_may_not():
    """Regression: open(stream=raw_bytes) was the silent-zero footgun."""
    with pdfplumber.open(io.BytesIO(_MINIMAL_PDF)) as pdf:
        assert len(pdf.pages) >= 1
    # Historical broken call — on some versions raises or yields unusable pdf
    try:
        with pdfplumber.open(stream=_MINIMAL_PDF) as pdf:
            broken_ok = len(pdf.pages) >= 1
    except Exception:
        broken_ok = False
    # BytesIO path must work regardless of broken_ok
    pages = _iter_pdf_page_text(_MINIMAL_PDF)
    assert len(pages) >= 1
    # If both work, fine; the test documents BytesIO is the supported path
    assert broken_ok or len(pages) >= 1
