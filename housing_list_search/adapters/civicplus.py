"""
CivicPlus Municipal CMS Adapter

Handles the Santa Clara County cities whose affordable-housing data lives in
the CivicPlus platform's "published documents" (DocumentCenter viewers, PDF
flyers) and Froala-rendered content blocks, behind CDN/WAF protection rather
than public HTML or JSON. Current consumers (TARGETS.md rows with the
`civicplus` measure; `cdn` is the legacy alias): Gilroy, Campbell, Los Altos,
Los Gatos.

This adapter is deliberately scoped to this county and these site patterns.
It is NOT a general self-discovering scraper — per-city quirks live in the
small config tables at the top of this module, not in branching logic.

What it does, in order:
1. Loads the human-facing page in headless Playwright with realistic headers.
2. Parses Froala availability blocks ("Property Name - N available units").
3. Extracts any HTML tables on the page.
4. Harvests per-property "Official Flyer" links with their property names.
5. Follows discovered DocumentCenter / PDF links (capped at max_documents)
   and extracts tables, viewer titles, and PDF flyer data.
6. Merges property names harvested from list pages into PDF-derived records.

Out of scope: login walls, CAPTCHA solving, fingerprint evasion.

Public entry point:
    extract_underlying_records(source, authority="", ...)

Normalization into the HousingRecord shape happens outside this adapter.
"""

from __future__ import annotations

import logging
import random
import re
import time
from datetime import datetime as _dt
from typing import Any
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeout

from housing_list_search.access import browser_page, is_safe_http_url, safe_goto

logger = logging.getLogger(__name__)

# ---------------------------------------------------------------------------
# Per-city configuration — the only place city-specific knowledge belongs.
# ---------------------------------------------------------------------------

# Seed documents whose links are not reliably machine-readable on the landing
# page. Keyed by source-page hostname.
CITY_SEED_DOCUMENTS: dict[str, list[str]] = {
    "www.cityofgilroy.org": [
        # Wheeler Manor availability flyer — referenced from the human-visible
        # list but the link is JS-rendered and invisible to the scraper.
        "https://www.cityofgilroy.org/DocumentCenter/View/16932",
    ],
}

# href substrings that mark a link as a published document worth following.
DOCUMENT_LINK_HINTS = (
    "showpublisheddocument",
    "publisheddocument",
    "documentcenter",
    "docaccess",
    ".pdf",
)

# Link text that marks a per-property availability flyer.
FLYER_TEXT_HINTS = ("official flyer", "50%ami", "60%ami", "50ami", "60ami")

# Hosts that are administrators with their own adapters — never treat their
# links as published documents.
EXCLUDED_HOSTS = ("housinginc.org",)

# "Property Name - 3 available units" as it appears on availability list pages.
_AVAILABILITY_RE = re.compile(
    r"([A-Z][A-Za-z][A-Za-z ]+?)\s*[-–]\s*(\d+)\s*available", re.IGNORECASE
)


def _jitter(seconds: float = 1.0) -> None:
    """Light human-like delay between page actions."""
    time.sleep(seconds + random.uniform(0.3, 1.2))


def _source_slug(authority: str) -> str:
    return f"civicplus:{authority.lower().replace(' ', '_').replace('.', '')}"


def _base_record(authority: str, method: str, source_url: str) -> dict[str, Any]:
    now = _dt.now().isoformat()
    return {
        "authority": authority,
        "extraction_method": method,
        "source_url": source_url,
        "source": _source_slug(authority),
        "last_seen": now,
        "first_seen": now,
    }


def _is_document_candidate(url: str) -> bool:
    lower = (url or "").strip().lower()
    if not lower or lower.startswith(("mailto:", "tel:", "javascript:")):
        return False
    if any(host in lower for host in EXCLUDED_HOSTS):
        return False
    return is_safe_http_url(url, resolve_dns=False)


def _dedupe_document_urls(urls: list[str]) -> list[str]:
    out: list[str] = []
    for u in urls:
        if _is_document_candidate(u) and u not in out:
            out.append(u)
    return out


def _get_realistic_headers() -> dict[str, str]:
    """Basic header realism without overcomplicating things."""
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,image/avif,image/webp,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
        "Accept-Encoding": "gzip, deflate, br",
        "DNT": "1",
        "Connection": "keep-alive",
        "Upgrade-Insecure-Requests": "1",
    }


def _parse_froala_availability_blocks(container, authority: str) -> list[dict]:
    """Parse Froala-generated availability lists.

    These appear as repeated <ul role="presentation"> blocks (one per listing),
    each containing a <li> whose <strong> holds the full line:
        "The Cannery Apartments - 5 available units"

    This pattern is common on Froala-powered municipal sites.
    """
    records = []
    for ul in container.find_all("ul", attrs={"role": "presentation"}):
        for li in ul.find_all("li"):
            strong = li.find("strong")
            if not strong:
                continue

            strong_text = strong.get_text(" ", strip=True)

            # Match "Name - 5 available units" or "Name – 3 units available"
            units_match = re.search(r"(\d+)\s*(?:available\s*)?units?", strong_text, re.IGNORECASE)
            if not units_match:
                units_match = re.search(
                    r"units?\s*(?:available)?:?\s*(\d+)", strong_text, re.IGNORECASE
                )

            units = units_match.group(1) if units_match else None
            if not units:
                continue

            # Strip the units portion to get a clean property name
            property_name = re.sub(
                r"\s*[-–—]?\s*\d+\s*(?:available\s*)?units?.*$",
                "",
                strong_text,
                flags=re.IGNORECASE,
            ).strip()

            # Pull contact info from the whole <li>
            full_text = li.get_text(" ", strip=True)
            email_match = re.search(r"[\w\.-]+@[\w\.-]+\.\w+", full_text)
            phone_match = re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", full_text)

            record = _base_record(authority, "froala_availability_list", "")
            record.update(
                {
                    "property_name": property_name,
                    "available_units": units,
                    "email": email_match.group(0) if email_match else "",
                    "phone": phone_match.group(0) if phone_match else "",
                }
            )
            records.append(record)

    return records


def _extract_tables(soup, authority: str, source_url: str, method: str) -> list[dict]:
    """Extract every HTML table into row dicts keyed by cleaned header names."""
    records: list[dict] = []
    for table in soup.find_all("table"):
        headers: list[str] = []
        for i, row in enumerate(table.find_all("tr")):
            cells = [
                c.get_text(strip=True) for c in row.find_all(["th", "td"]) if c.get_text(strip=True)
            ]
            if not cells:
                continue
            if i == 0 and not headers:
                # Municipal published documents sometimes cram several header
                # names into one cell — split on common separators.
                for h in cells:
                    parts = re.split(r"[,/|&]|\s{2,}", h)
                    headers.extend(p.strip() for p in parts if p.strip())
                continue

            record = _base_record(authority, method, source_url)
            for j, text in enumerate(cells):
                key = headers[j] if j < len(headers) else f"col_{j}"
                clean_key = (
                    key.lower()
                    .replace(" ", "_")
                    .replace("/", "_")
                    .replace(".", "")
                    .replace(",", "")
                )
                record[clean_key] = text
            if len(record) > 6:  # more than just the base fields
                records.append(record)
    return records


def _nearest_property_name(a_tag) -> str:
    """Walk up from a flyer link looking for 'Property Name - N available' text."""
    node = a_tag
    for _ in range(8):
        if node is None:
            return ""
        m = _AVAILABILITY_RE.search(node.get_text(" ", strip=True))
        if m:
            return m.group(1).strip()
        node = getattr(node, "parent", None)
    return ""


def _name_from_documentcenter_slug(url: str) -> str:
    """Derive 'Wheeler Manor (50% AMI)' from /DocumentCenter/View/16932/Wheeler-Manor-Flyer_50AMI."""
    m = re.search(r"/DocumentCenter/View/\d+/([A-Za-z0-9_-]+)", url)
    if not m:
        return ""
    slug = m.group(1).replace("-", " ").replace("_", " ")
    slug = re.sub(r"\s*(Flyer|Event|Calendar)\s*", " ", slug, flags=re.I).strip()
    slug = re.sub(r"\s*(\d+)\s*(?:%\s*)?ami\s*$", r" (\1% AMI)", slug, flags=re.I).strip()
    return slug


def _discover_document_links(soup, page_url: str) -> list[str]:
    """Collect absolutized published-document links from a rendered page.
    Restricts to same-domain or known doc hosts (docaccess, documentcenter) to
    limit risk from discovered links (see #407). Non-doc external links are skipped.
    """
    base = urlparse(page_url).netloc.lower()
    found: list[str] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if any(hint in href.lower() for hint in DOCUMENT_LINK_HINTS):
            abs_url = urljoin(page_url, href)
            if _is_document_candidate(abs_url):
                p = urlparse(abs_url)
                host = p.netloc.lower()
                if (
                    host == base
                    or (host and host.endswith("." + base))
                    or "documentcenter" in host
                    or "docaccess" in host
                    or "showpublisheddocument" in href.lower()
                ):
                    found.append(abs_url)
    return found


def _harvest_flyer_links(soup, page_url: str, authority: str) -> list[dict]:
    """Capture (property_name, flyer URL) pairs from an availability list page.

    Gilroy-style list pages repeat blocks of:
        "The Cannery Apartments - 5 available units"
        contact email / phone
        "Official Flyer" / "Official Flyer 50%AMI" links → DocumentCenter
    """
    records: list[dict] = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if "documentcenter/view" not in href.lower():
            continue
        link_text = a.get_text(strip=True)
        if not any(hint in link_text.lower() for hint in FLYER_TEXT_HINTS):
            continue

        full = urljoin(page_url, href)
        prop_name = _nearest_property_name(a) or _name_from_documentcenter_slug(full)

        record = _base_record(authority, "availability_list_flyer", page_url)
        record.update(
            {
                "property_name": prop_name,
                "flyer_text": link_text,
                "flyer_url": full,
            }
        )
        records.append(record)
    return records


def _looks_like_flyer(url: str) -> bool:
    lower = url.lower()
    return "flyer" in lower or any(hint in lower for hint in ("50ami", "60ami", "ami"))


def _process_pdfs(
    pdf_urls: list[str],
    authority: str,
    *,
    extraction_errors: list[bool] | None = None,
) -> list[dict]:
    """Run discovered PDF flyers through the PDF extraction layer."""
    from housing_list_search.extraction.pdf import extract_records_from_pdf

    records: list[dict] = []
    for pdf_url in pdf_urls:
        try:
            logger.info("[civicplus] Extracting PDF: %s", pdf_url)
            for rec in extract_records_from_pdf(pdf_url, authority):
                r = rec.to_dict()
                base = _base_record(authority, r.get("extraction_method", "pdf_flyer"), pdf_url)
                for k, v in base.items():
                    r.setdefault(k, v)

                # If the parser produced a weak name, the DocumentCenter URL
                # slug is usually a better signal.
                if not r.get("property_name") or len(str(r.get("property_name", ""))) < 5:
                    slug_name = _name_from_documentcenter_slug(pdf_url)
                    if slug_name:
                        r["property_name"] = slug_name

                records.append(r)
        except Exception as exc:
            if extraction_errors is not None:
                extraction_errors[0] = True
            logger.warning("[civicplus] Failed to extract PDF %s: %s", pdf_url, exc)
    return records


def extract_underlying_records(
    source: str,
    authority: str = "",
    timeout: int = 45000,
    max_documents: int = 5,
    known_document_urls: list[str] | None = None,
) -> list[dict[str, Any]]:
    """
    Reach a CDN/WAF-protected city page and extract the underlying records
    from the page itself and the published documents it links to.

    Args:
        source: The human-facing URL (e.g. Gilroy Housing & Community Services).
        authority: Name of the city/authority (for traceability).
        timeout: Playwright navigation timeout in milliseconds.
        max_documents: Cap on how many discovered documents to process.
        known_document_urls: Extra document URLs to process alongside whatever
            the page discovery finds.

    Returns:
        List of raw-ish dicts (one per row/item). Normalization happens later.
    """
    logger.info("[civicplus] Starting extract_underlying_records for %s", authority or source)

    records: list[dict[str, Any]] = []
    extraction_errors = [False]
    document_urls: list[str] = list(known_document_urls or [])

    host = urlparse(source).netloc.lower()
    for seed in CITY_SEED_DOCUMENTS.get(host, []):
        document_urls.append(seed)

    # A direct document link as the source skips the landing-page pass entirely.
    direct_document_mode = "/documentcenter/view/" in source.lower() or source.lower().endswith(
        ".pdf"
    )
    if direct_document_mode:
        document_urls.append(source)
        logger.info("[civicplus] Source is a direct document link — skipping landing page")

    document_urls = _dedupe_document_urls(document_urls)

    try:
        with browser_page(
            extra_http_headers=_get_realistic_headers(),
            viewport={"width": 1366, "height": 768},
            locale="en-US",
            timezone_id="America/Los_Angeles",
        ) as page:
            # --- 1. Landing page: availability blocks, tables, flyer links ---
            if not direct_document_mode:
                _jitter(0.8)
                safe_goto(page, source, wait_until="domcontentloaded", timeout=timeout)
                _jitter(1.0)

                # Human-like scroll passes help Froala/lazy content render.
                page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                _jitter(1.0)
                page.evaluate("window.scrollTo(0, 0)")

                try:
                    page.wait_for_load_state("networkidle", timeout=15000)
                except PlaywrightTimeout:
                    pass
                try:
                    page.wait_for_selector("div.fr-view, ul[role='presentation']", timeout=10000)
                except PlaywrightTimeout:
                    pass

                soup = BeautifulSoup(page.content(), "html.parser")
                main_content = soup.find("div", class_="fr-view") or soup

                froala_records = _parse_froala_availability_blocks(main_content, authority)
                for r in froala_records:
                    r["source_url"] = source
                records.extend(froala_records)

                records.extend(_extract_tables(soup, authority, source, "direct_page_table"))
                flyer_records = _harvest_flyer_links(soup, page.url, authority)
                records.extend(flyer_records)

                document_urls.extend(r["flyer_url"] for r in flyer_records)
                document_urls.extend(_discover_document_links(soup, page.url))
                document_urls = _dedupe_document_urls(document_urls)
                logger.info(
                    "[civicplus] %s: %d page records, %d candidate documents",
                    authority,
                    len(records),
                    len(document_urls),
                )

            # --- 2. Discovered documents: viewer pages and PDF flyers ---
            pdf_urls = [u for u in document_urls if u.lower().endswith(".pdf")]
            html_doc_urls = [u for u in document_urls if not u.lower().endswith(".pdf")]
            # Property/AMI flyers are the highest-value documents — process first.
            pdf_urls.sort(key=lambda u: 0 if _looks_like_flyer(u) else 1)
            html_doc_urls.sort(key=lambda u: 0 if _looks_like_flyer(u) else 1)

            for doc_url in html_doc_urls[:max_documents]:
                _jitter(0.9)
                try:
                    safe_goto(page, doc_url, wait_until="domcontentloaded", timeout=timeout)
                    _jitter(1.0)
                    doc_soup = BeautifulSoup(page.content(), "html.parser")

                    records.extend(_extract_tables(doc_soup, authority, doc_url, "document_table"))
                    flyers = _harvest_flyer_links(doc_soup, page.url, authority)
                    records.extend(flyers)
                    pdf_urls.extend(
                        f["flyer_url"] for f in flyers if f["flyer_url"].lower().endswith(".pdf")
                    )

                    # DocumentCenter viewer pages carry the document title even
                    # when the content itself is a PDF embed.
                    if "documentcenter" in doc_url.lower():
                        h = doc_soup.find(["h1", "h2", "h3"]) or doc_soup.find("title")
                        title = h.get_text(strip=True) if h else ""
                        if title:
                            record = _base_record(authority, "documentcenter_viewer", doc_url)
                            record["property_name"] = title
                            records.append(record)

                except PlaywrightTimeout:
                    extraction_errors[0] = True
                    logger.warning("[civicplus] Timeout on document page: %s", doc_url)
                except Exception as exc:
                    if "Download is starting" in str(exc):
                        # Navigation triggered a file download — treat as a PDF.
                        pdf_urls.append(doc_url)
                    else:
                        extraction_errors[0] = True
                        logger.warning("[civicplus] Error on document page %s: %s", doc_url, exc)

            records.extend(
                _process_pdfs(
                    _dedupe_document_urls(pdf_urls)[:max_documents],
                    authority,
                    extraction_errors=extraction_errors,
                )
            )

            # --- 3. Merge list-page property names into PDF-derived records ---
            flyer_context = {
                r["flyer_url"]: r["property_name"]
                for r in records
                if r.get("extraction_method") == "availability_list_flyer"
                and r.get("flyer_url")
                and r.get("property_name")
            }
            for r in records:
                if r.get("source_url") in flyer_context and (
                    not r.get("property_name") or len(str(r.get("property_name", ""))) < 5
                ):
                    r["property_name"] = flyer_context[r["source_url"]]

            if not records:
                try:
                    debug_slug = re.sub(r"[^\w]+", "_", (authority or "unknown").lower()).strip("_")
                    debug_path = f"/tmp/{debug_slug}_civicplus_debug.png"
                    page.screenshot(path=debug_path, full_page=True)
                    logger.warning(
                        "[civicplus] %s: 0 records extracted — debug screenshot at %s (page title: %r)",
                        authority,
                        debug_path,
                        page.title(),
                    )
                except Exception:
                    logger.warning(
                        "[civicplus] %s: 0 records extracted (screenshot failed)", authority
                    )

    except Exception as exc:
        logger.exception(
            "[civicplus] Error during extraction for %s: %s", authority or source, exc
        )
        raise

    if not records and extraction_errors[0]:
        raise RuntimeError(
            f"[civicplus] {authority or source}: zero records after extraction errors"
        )

    logger.info(
        "[civicplus] Extracted %d underlying records from %s", len(records), authority or source
    )
    return records
