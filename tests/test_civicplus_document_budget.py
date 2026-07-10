"""CivicPlus inventory-first document budget (Gilroy/LG max_documents self-shot)."""

from __future__ import annotations

from housing_list_search.adapters.civicplus import (
    DEFAULT_MAX_DOCUMENTS,
    _is_non_inventory_pdf,
    inventory_document_overflow,
    partition_document_candidates,
    rank_inventory_documents,
)


def test_default_max_documents_covers_gilroy_797_and_lg():
    """Probe 2026-07-10: Gilroy /797 had 9 cands; LG ~14 inventory after filter."""
    assert DEFAULT_MAX_DOCUMENTS >= 15


def test_partition_drops_noise_not_inventory():
    urls = [
        "https://www.cityofgilroy.org/DocumentCenter/View/16932",
        "https://www.losgatosca.gov/DocumentCenter/View/38285/Ord-2313---Amending-Chapter-29",
        "https://www.cityofgilroy.org/DocumentCenter/View/5498/Police-Records---Application-for-Release",
        "https://city.example/DocumentCenter/View/1/affordable-rental-flyer.pdf",
        "https://www.losgatosca.gov/DocumentCenter/View/38286/2020-040---Approving-Modifications-to-the-Below-Market-Price-Housing-Program-and-Guidelines",
    ]
    inventory, noise = partition_document_candidates(urls)
    assert "https://www.cityofgilroy.org/DocumentCenter/View/16932" in inventory
    assert any("flyer" in u for u in inventory)
    assert any("Ord-2313" in u for u in noise)
    assert any("Police-Records" in u for u in noise)
    assert any("Approving-Modifications" in u for u in noise)
    # Noise must not count toward budget
    assert inventory_document_overflow(len(inventory), 5) == 0


def test_hub_page_style_list_fails_only_on_inventory_overflow():
    """Department hub with many bare View/IDs still exceeds budget → fail-loud OK."""
    hub = [f"https://www.cityofgilroy.org/DocumentCenter/View/{i}" for i in range(1000, 1023)]
    inventory, noise = partition_document_candidates(hub)
    assert noise == []
    assert inventory_document_overflow(len(inventory), DEFAULT_MAX_DOCUMENTS) == 8
    assert inventory_document_overflow(len(inventory), 5) == 18


def test_gilroy_797_style_fits_default_budget():
    """Affordable Apartments page: ~9 docs with 1 police noise → under default cap."""
    cands = [
        "https://www.cityofgilroy.org/DocumentCenter/View/16932",
        "https://www.cityofgilroy.org/DocumentCenter/View/20268",
        "https://www.cityofgilroy.org/DocumentCenter/View/20186",
        "https://www.cityofgilroy.org/DocumentCenter/View/20187",
        "https://www.cityofgilroy.org/DocumentCenter/View/20212",
        "https://www.cityofgilroy.org/DocumentCenter/View/16933",
        "https://www.cityofgilroy.org/DocumentCenter/View/16517",
        "https://www.cityofgilroy.org/DocumentCenter/View/16518",
        "https://www.cityofgilroy.org/DocumentCenter/View/5498/Police-Records---Application-for-Release-of-Information-Police-Report-Request",
    ]
    inventory, noise = partition_document_candidates(cands)
    assert len(noise) == 1
    assert len(inventory) == 8
    assert inventory_document_overflow(len(inventory), DEFAULT_MAX_DOCUMENTS) == 0
    # Old global cap of 5 would still have failed even inventory-only
    assert inventory_document_overflow(len(inventory), 5) == 3


def test_rank_prefers_flyer_urls():
    urls = [
        "https://x/DocumentCenter/View/1/program-doc",
        "https://x/DocumentCenter/View/2/50ami-flyer.pdf",
        "https://x/DocumentCenter/View/3/other",
    ]
    ranked = rank_inventory_documents(urls)
    assert ranked[0].endswith("50ami-flyer.pdf")
    assert ranked[1].endswith("program-doc")
    assert ranked[2].endswith("other")


def test_ordinance_still_non_inventory():
    assert _is_non_inventory_pdf(
        "https://www.losgatosca.gov/DocumentCenter/View/38285/"
        "Ord-2313---Amending-Chapter-29-Zoning-Regulations"
    )
