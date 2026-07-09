"""PDF magic / content-type guard (#791)."""

from housing_list_search.extraction.pdf import _looks_like_pdf


def test_pdf_magic_accepts_percent_pdf():
    assert _looks_like_pdf(b"%PDF-1.4\n...") is True


def test_pdf_magic_rejects_html():
    assert _looks_like_pdf(b"<!DOCTYPE html><html>", content_type="text/html") is False


def test_pdf_magic_rejects_empty():
    assert _looks_like_pdf(b"") is False
