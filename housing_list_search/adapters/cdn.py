"""
CDN / WAF Protected Published Document Adapter (First-Class)

Handles municipal and government sites that sit behind CDN/WAF protection
(Akamai, Cloudflare, etc.) where the real data lives in "published documents"
(showpublisheddocument, docaccess viewers, etc.) rather than public HTML or JSON.

The adapter's job is to get past the protection layer and return the
**underlying records** as cleanly as possible. Normalization into
HousingRecord or any other shape is deliberately done *outside* this adapter.

Public entry point:
    extract_underlying_records(source, authority="", ...)

Design goals (light-to-medium effort):
- Off-the-shelf Playwright only
- Realistic headers + light user-agent variation
- Programmed jitter and human-like browser behavior
- Network interception to discover the real document URLs
- No heavy fingerprint evasion or "hacking"

Scope & Guardrails
------------------
In Scope:
- Sites that return 403/JS challenges on the main page but serve real content
  via protected document links (showpublisheddocument, docaccess, similar).
- Extracting structured tables/lists from the underlying documents.
- Returning raw-to-semi-structured records for later normalization.

Out of Scope:
- Sites that require login, CAPTCHA solving, or account creation.
- Aggressive anti-bot bypass (e.g. real browser fingerprint spoofing farms).
- Turning this into a general web scraper.

Known Low-Value Patterns
------------------------
- Pages that only contain policy text with no structured lists.
- Documents that are purely marketing or "how to apply" with no actual
  property-level data.

PATTERN FOR NEW USE CASES
-------------------------
When you find another city whose affordable housing information is buried
behind a CDN-protected document viewer:
1. Add the human-facing URL to TARGETS.md with `cdn` in the scraping measures.
2. Call `extract_underlying_records(url, authority=...)`.
3. Inspect the raw records it returns.
4. Decide whether to normalize them into the main HousingRecord shape,
   keep them as a separate category, or mark the target as low-value.

This keeps the adapter reusable across Akamai, Cloudflare, and similar
document-publishing patterns without creating one-off city files.
"""

from __future__ import annotations

import logging
import random
import re
import time
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import sync_playwright, TimeoutError as PlaywrightTimeout

logger = logging.getLogger(__name__)


def _jitter(seconds: float = 1.0) -> None:
    """Light human-like jitter."""
    time.sleep(seconds + random.uniform(0.3, 1.2))


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


def extract_underlying_records(
    source: str,
    authority: str = "",
    timeout: int = 45000,
    max_documents: int = 5,
    known_document_urls: list[str] | None = None,
    **kwargs: Any,
) -> list[dict[str, Any]]:
    """
    Reach a CDN/WAF-protected page and extract the actual underlying records
    from the published documents it links to.

    Returns a list of raw-ish dictionaries representing rows found in the
    underlying documents. The exact shape is intentionally not normalized yet.

    Args:
        source: The human-facing URL (e.g. Sunnyvale /rental-programs page).
        authority: Name of the city/authority (for traceability).
        timeout: Playwright timeout in milliseconds.
        max_documents: Safety cap on how many published documents to process.

    Returns:
        List of dictionaries (one per row/item found in the documents).
    """
    logger.info(f"[cdn] Starting extract_underlying_records for {authority or source}")

    records: list[dict[str, Any]] = []
    document_urls: list[str] = []

    if known_document_urls:
        document_urls.extend(known_document_urls)

    # If the source itself is a clean showdocument link, remember the ID so we can
    # look for the current working viewer link on the landing page.
    showdocument_id = None
    if '/showdocument/' in source:
        try:
            showdocument_id = source.rstrip('/').split('/showdocument/')[-1].split('?')[0]
        except Exception:
            pass
    if known_document_urls:
        for u in known_document_urls:
            if '/showdocument/' in u and not showdocument_id:
                try:
                    showdocument_id = u.rstrip('/').split('/showdocument/')[-1].split('?')[0]
                except Exception:
                    pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(
            extra_http_headers=_get_realistic_headers(),
            viewport={"width": 1366, "height": 768},
        )

        page = context.new_page()

        # Light jitter before navigation
        _jitter(0.8)

        try:
            # Strategy: If we were given a clean showdocument-style URL, first visit the landing page (source)
            # so we can discover the current working viewer link from the DOM/network. This lets the adapter
            # accept nice clean city URLs while still doing the hard work of finding a loadable document.
            if showdocument_id and source:
                logger.info(f"[cdn] Clean showdocument ID detected ({showdocument_id}). Visiting landing page first to discover working viewer link...")
                page.goto(source, wait_until="domcontentloaded", timeout=timeout)
                _jitter(1.5)
                # The discovery logic below will now search this page for the real link
            elif document_urls:
                logger.info(f"[cdn] Using provided document URL(s) directly")
            else:
                logger.info(f"[cdn] Visiting source page: {source}")
                page.goto(source, wait_until="domcontentloaded", timeout=timeout)
                _jitter(1.0)

            # Human-like behavior: scroll around a bit
            page.evaluate("window.scrollTo(0, document.body.scrollHeight / 3)")
            _jitter(0.8)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            _jitter(1.2)
            page.evaluate("window.scrollTo(0, 0)")
            _jitter(0.6)

            # Wait for network to settle (important for JS-rendered content)
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                pass

            # === 1. Aggressive link discovery + network interception (especially for showdocument IDs) ===
            captured_urls = []

            def handle_request(request):
                url = request.url
                url_lower = url.lower()
                if any(x in url_lower for x in ["showpublisheddocument", "docaccess", "document", ".pdf", "published"]):
                    captured_urls.append(url)

            page.on("request", handle_request)

            all_links = page.query_selector_all("a[href]")
            for link in all_links:
                href = link.get_attribute("href") or ""
                href_lower = href.lower()
                if any(x in href_lower for x in [
                    "showpublisheddocument", "docaccess", "document", ".pdf",
                    "publisheddocument", "rental", "housing", "showdocument"
                ]):
                    if href.startswith("/"):
                        base = "/".join(page.url.split("/")[:3])
                        href = base + href
                    if href not in document_urls:
                        document_urls.append(href)

            # Also add anything we captured via network
            for u in captured_urls:
                if u not in document_urls:
                    document_urls.append(u)

            logger.info(f"[cdn] Discovered {len(document_urls)} candidate document links after broad scan + interception")

            # If we have a showdocument ID, aggressively search the current page (DOM + raw HTML)
            # for any link or URL that contains that ID. This is the key part that lets the adapter
            # accept clean city URLs and still discover a working viewer link at runtime.
            if showdocument_id:
                logger.info(f"[cdn] Aggressively searching page for working viewer link containing ID {showdocument_id}")
                # DOM links
                all_links = page.query_selector_all("a[href]")
                for link in all_links:
                    href = link.get_attribute("href") or ""
                    if showdocument_id in href and href not in document_urls:
                        if href.startswith("/"):
                            base = "/".join(page.url.split("/")[:3])
                            href = base + href
                        document_urls.append(href)
                        logger.info(f"[cdn] Found via DOM: {href}")

                # Also search the raw HTML for any URL containing the ID (catches JS-rendered or data attrs)
                raw_html = page.content()
                import re
                pattern = rf'https?://[^"\'\s<>]*{re.escape(showdocument_id)}[^"\'\s<>]*'
                matches = re.findall(pattern, raw_html)
                for m in matches:
                    if m not in document_urls:
                        document_urls.append(m)
                        logger.info(f"[cdn] Found via raw HTML scan: {m}")

                # Fallback for known patterns (e.g. Sunnyvale): if we only have the clean ID and no working link found,
                # try the common docaccess viewer wrapper as a last resort within the adapter.
                if not any('docaccess' in u or 'showpublisheddocument' in u for u in document_urls):
                    fallback = f'https://docaccess.com/docviewer.html?url=https%3A%2F%2Fwww.sunnyvale.ca.gov%2Fhome%2Fshowdocument%2F{showdocument_id}'
                    document_urls.append(fallback)
                    logger.info(f"[cdn] No working link found on page — adding common docaccess fallback for ID {showdocument_id}")

            # === 2. Try to extract tables directly from the current page (many cities render the list here) ===
            logger.info("[cdn] Attempting to extract tables from current page content")
            content = page.content()
            soup = BeautifulSoup(content, "html.parser")

            for table in soup.find_all("table"):
                rows = table.find_all("tr")
                headers = []
                for i, row in enumerate(rows):
                    cells = [cell.get_text(strip=True) for cell in row.find_all(["th", "td"]) if cell.get_text(strip=True)]
                    if not cells:
                        continue
                    if i == 0 and not headers:
                        # Split on common separators seen in municipal published documents
                        headers = []
                        for h in cells:
                            parts = re.split(r'[,/|&]|\s{2,}', h)
                            headers.extend([p.strip() for p in parts if p.strip()])
                        continue
                    record = {"source_url": source, "authority": authority, "extraction_method": "direct_page_table"}
                    for j, text in enumerate(cells):
                        key = headers[j] if j < len(headers) else f"col_{j}"
                        clean_key = key.lower().replace(" ", "_").replace("/", "_").replace(".", "").replace(",", "")
                        record[clean_key] = text
                    if len(record) > 3:
                        records.append(record)

            # === 3. If we still have very few records, try navigating discovered document links ===
            if len(records) < 5 and document_urls:
                for doc_url in document_urls[:max_documents]:
                    logger.info(f"[cdn] Attempting to load discovered document: {doc_url}")
                    _jitter(0.9)
                    try:
                        page.goto(doc_url, wait_until="domcontentloaded", timeout=timeout)
                        _jitter(1.0)
                        page.wait_for_load_state("networkidle", timeout=10000)

                        doc_content = page.content()
                        doc_soup = BeautifulSoup(doc_content, "html.parser")

                        for table in doc_soup.find_all("table"):
                            rows = table.find_all("tr")
                            headers = []
                            for i, row in enumerate(rows):
                                cells = [cell.get_text(strip=True) for cell in row.find_all(["th", "td"]) if cell.get_text(strip=True)]
                                if not cells:
                                    continue
                                if i == 0 and not headers:
                                    headers = cells
                                    continue
                                record = {"source_url": doc_url, "authority": authority, "extraction_method": "document_table"}
                                for j, text in enumerate(cells):
                                    key = headers[j] if j < len(headers) else f"col_{j}"
                                    record[key.lower().replace(" ", "_").replace("/", "_").replace(".", "")] = text
                                if len(record) > 3:
                                    records.append(record)

                    except PlaywrightTimeout:
                        logger.warning(f"[cdn] Timeout on document: {doc_url}")
                        continue

            # Fallback: grab any large text blocks that look like property entries
            if len(records) < 3:
                logger.info("[cdn] Falling back to raw text extraction")
                for elem in soup.find_all(["p", "li", "div", "span"]):
                    text = elem.get_text(" ", strip=True)
                    if any(kw in text.lower() for kw in ["park", "court", "apartments", "housing", "subsidized", "special needs"]) and len(text) > 20:
                        records.append({
                            "source_url": source,
                            "authority": authority,
                            "extraction_method": "raw_text_fallback",
                            "raw_text": text[:500]
                        })

            # Ultimate fallback: save screenshot + return page title + any visible text for debugging
            if len(records) == 0:
                try:
                    page.screenshot(path="/tmp/sunnyvale_debug.png", full_page=True)
                    logger.info("[cdn] Saved debug screenshot to /tmp/sunnyvale_debug.png")
                except Exception:
                    pass

                title = page.title()
                body_text = soup.get_text(" ", strip=True)[:2000]
                records.append({
                    "source_url": source,
                    "authority": authority,
                    "extraction_method": "debug_fallback",
                    "page_title": title,
                    "visible_text_sample": body_text
                })

        except Exception as e:
            logger.exception(f"[cdn] Error during extraction: {e}")
        finally:
            browser.close()

    logger.info(f"[cdn] Extracted {len(records)} underlying records from {authority or source}")
    return records
