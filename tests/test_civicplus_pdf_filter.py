"""#1091: CivicPlus must not cascade non-inventory PDFs (ordinances, etc.)."""

from housing_list_search.adapters.civicplus import (
    _is_non_inventory_pdf,
    _looks_like_flyer,
    _process_pdfs,
)


def test_ordinance_slug_is_non_inventory():
    url = (
        "https://www.losgatosca.gov/DocumentCenter/View/38285/"
        "Ord-2313---Amending-Chapter-29-Zoning-Regulations-Regarding-the-Below-Market-Price-BMP-Program"
    )
    assert _is_non_inventory_pdf(url) is True


def test_flyer_url_is_inventory_candidate():
    assert _is_non_inventory_pdf("https://city.example/DocumentCenter/View/1/affordable-flyer") is False
    assert _looks_like_flyer("https://city.example/50ami-flyer.pdf") is True


def test_process_pdfs_skips_ordinance(monkeypatch):
    called: list[str] = []

    def fake_extract(url, authority):
        called.append(url)
        return []

    monkeypatch.setattr(
        "housing_list_search.extraction.pdf.extract_records_from_pdf",
        fake_extract,
    )
    urls = [
        "https://x/DocumentCenter/View/1/Ord-99-Zoning",
        "https://x/DocumentCenter/View/2/rental-flyer.pdf",
    ]
    _process_pdfs(urls, "City of Test")
    assert called == ["https://x/DocumentCenter/View/2/rental-flyer.pdf"]
