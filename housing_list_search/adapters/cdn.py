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
from datetime import datetime as _dt
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
            units_match = re.search(r'(\d+)\s*(?:available\s*)?units?', strong_text, re.IGNORECASE)
            if not units_match:
                units_match = re.search(r'units?\s*(?:available)?:?\s*(\d+)', strong_text, re.IGNORECASE)

            units = units_match.group(1) if units_match else None
            if not units:
                continue

            # Strip the units portion to get a clean property name
            property_name = re.sub(r'\s*[-–—]?\s*\d+\s*(?:available\s*)?units?.*$', '', strong_text, flags=re.IGNORECASE).strip()

            # Pull contact info from the whole <li>
            full_text = li.get_text(" ", strip=True)
            email_match = re.search(r'[\w\.-]+@[\w\.-]+\.\w+', full_text)
            phone_match = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', full_text)

            record = {
                "source_url": "",  # filled by caller if needed
                "authority": authority,
                "extraction_method": "froala_availability_list",
                "property_name": property_name,
                "available_units": units,
                "email": email_match.group(0) if email_match else "",
                "phone": phone_match.group(0) if phone_match else "",
                "last_seen": _dt.now().isoformat(),
                "first_seen": _dt.now().isoformat(),
                "source": f"cdn:{authority.lower().replace(' ', '_').replace('.', '')}",
            }
            records.append(record)

    return records


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

        # Capture any JSON responses that look like they might contain availability data
        captured_json = []

        def _on_response(response):
            try:
                if "json" in response.headers.get("content-type", "").lower():
                    url = response.url.lower()
                    if any(kw in url for kw in ["housing", "available", "units", "bmr", "affordable", "list"]):
                        data = response.json()
                        if isinstance(data, (list, dict)):
                            captured_json.append({"url": response.url, "data": data})
            except Exception:
                pass  # ignore parsing errors

        page.on("response", _on_response)

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

            # More patient wait for JS-heavy / Froala sites
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except Exception:
                pass

            # Try to wait for signals that the availability section has rendered
            try:
                page.wait_for_selector(
                    "div.fr-view, ul[role='presentation'], text='available units'",
                    timeout=12000
                )
            except Exception:
                pass

            # Keep trying for up to ~25s until the real availability lists appear
            # (the ones with <strong> containing "Property - X available units" inside role="presentation" uls)
            try:
                page.wait_for_function("""
                    () => {
                        const lists = document.querySelectorAll("ul[role='presentation']");
                        let found = 0;
                        for (let ul of lists) {
                            const strongs = ul.querySelectorAll("strong");
                            for (let s of strongs) {
                                const t = s.innerText.toLowerCase();
                                if ((t.includes('available') || t.includes('units')) && 
                                    (t.includes('apartments') || t.includes('manor') || t.includes('gardens') || t.includes('housing'))) {
                                    found++;
                                }
                            }
                        }
                        return found >= 2;   // wait until we see at least a couple real listings
                    }
                """, timeout=25000, polling=500)
            except Exception:
                pass

            # Final broad text sweep after everything else: look for any text on the page that
            # looks like a real availability entry ("Property Name - 5 available units" style).
            # This is the last clean HTML attempt before we accept that the data may only be
            # in the linked PDFs or behind additional client-side rendering.
            try:
                page.wait_for_function("""
                    () => {
                        const bodyText = document.body.innerText.toLowerCase();
                        return bodyText.includes('available units') || bodyText.includes('units available');
                    }
                """, timeout=5000)
            except Exception:
                pass

            # One last attempt: after maximum waiting, walk the DOM one more time looking for
            # any text node that contains both a housing keyword and an availability indicator.
            # If we find strong candidates, try to turn them into lightweight records right here.
            try:
                final_candidates = page.evaluate("""
                    () => {
                        const results = [];
                        const walker = document.createTreeWalker(document.body, NodeFilter.SHOW_TEXT);
                        let node;
                        while ((node = walker.nextNode())) {
                            const t = node.textContent.trim();
                            if (t.length > 15 && 
                                (t.toLowerCase().includes('available') || t.toLowerCase().includes('units')) &&
                                (t.toLowerCase().includes('apartments') || t.toLowerCase().includes('manor') || 
                                 t.toLowerCase().includes('gardens') || t.toLowerCase().includes('housing'))) {
                                results.push(t.substring(0, 250));
                            }
                        }
                        return results.slice(0, 15);
                    }
                """)
                if final_candidates and len(final_candidates) > 0:
                    print("[DEBUG] Final broad text candidates found:", final_candidates)
                    # Attempt to turn promising strings into lightweight records
                    for candidate in final_candidates:
                        units_match = re.search(r'(\d+)\s*(?:available\s*)?units?', candidate, re.IGNORECASE)
                        if units_match:
                            units = units_match.group(1)
                            # Rough name extraction: take text before the units part
                            name = re.sub(r'\s*[-–—]?\s*\d+\s*(?:available\s*)?units?.*$', '', candidate, flags=re.IGNORECASE).strip()
                            if name and len(name) > 3:
                                records.append({
                                    "source_url": source,
                                    "authority": authority,
                                    "extraction_method": "final_text_sweep",
                                    "property_name": name,
                                    "available_units": units,
                                    "last_seen": _dt.now().isoformat(),
                                    "first_seen": _dt.now().isoformat(),
                                    "source": f"cdn:{authority.lower().replace(' ', '_').replace('.', '')}",
                                })
            except Exception:
                pass

            # Extra scroll + settle pass (some lists only appear after scrolling)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight * 0.6)")
            _jitter(1.0)
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            _jitter(1.2)
            page.evaluate("window.scrollTo(0, 0)")
            _jitter(0.8)

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
                    now_iso = _dt.now().isoformat()

                    record = {
                        "source_url": source,
                        "authority": authority,
                        "extraction_method": "direct_page_table",
                        "last_seen": now_iso,
                        "first_seen": now_iso,
                        "source": f"cdn:{authority.lower().replace(' ', '_').replace('.', '')}",
                        "source_url": source,
                    }
                    for j, text in enumerate(cells):
                        key = headers[j] if j < len(headers) else f"col_{j}"
                        clean_key = key.lower().replace(" ", "_").replace("/", "_").replace(".", "").replace(",", "")
                        record[clean_key] = text
                    if len(record) > 3:
                        records.append(record)

            # === 3. Process discovered document links (handle PDFs safely, avoid "Download is starting") ===
            if len(records) < 5 and document_urls:
                pdf_urls = []
                html_doc_urls = []

                for u in document_urls[:max_documents]:
                    if u.lower().endswith('.pdf'):
                        pdf_urls.append(u)
                    else:
                        html_doc_urls.append(u)

                # Process HTML document viewer pages
                for doc_url in html_doc_urls:
                    logger.info(f"[cdn] Attempting to load discovered document page: {doc_url}")
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
                                now_iso = _dt.now().isoformat()
                                record = {
                                    "authority": authority,
                                    "extraction_method": "document_table",
                                    "last_seen": now_iso,
                                    "first_seen": now_iso,
                                    "source": f"cdn:{authority.lower().replace(' ', '_').replace('.', '')}",
                                    "source_url": doc_url,
                                }
                                for j, text in enumerate(cells):
                                    key = headers[j] if j < len(headers) else f"col_{j}"
                                    clean_key = key.lower().replace(" ", "_").replace("/", "_").replace(".", "").replace(",", "")
                                    record[clean_key] = text
                                if len(record) > 3:
                                    records.append(record)

                    except PlaywrightTimeout:
                        logger.warning(f"[cdn] Timeout on document page: {doc_url}")
                        continue
                    except Exception as e:
                        if "Download is starting" in str(e):
                            logger.info(f"[cdn] Skipping direct PDF navigation: {doc_url}")
                            pdf_urls.append(doc_url)  # treat as PDF to extract later
                        else:
                            logger.warning(f"[cdn] Error on document page {doc_url}: {e}")
                            continue

                # Process PDFs using existing PDF extraction (no page navigation)
                if pdf_urls:
                    from housing_list_search.pdf_scraper import extract_from_pdf
                    for pdf_url in pdf_urls:
                        try:
                            logger.info(f"[cdn] Extracting PDF directly: {pdf_url}")
                            pdf_recs = extract_from_pdf(pdf_url, authority)
                            for r in pdf_recs:
                                r.setdefault("last_seen", _dt.now().isoformat())
                                r.setdefault("first_seen", _dt.now().isoformat())
                                r.setdefault("source", f"cdn:{authority.lower().replace(' ', '_').replace('.', '')}")
                                r.setdefault("source_url", pdf_url)
                            records.extend(pdf_recs)
                        except Exception as e:
                            logger.warning(f"[cdn] Failed to extract PDF {pdf_url}: {e}")

            # Fallback: focus on the main content area (fr-view for Froala sites like Gilroy)
            if len(records) < 5:
                logger.info("[cdn] Falling back to raw text extraction")

                main_content = soup.find("div", class_="fr-view") or soup

                # === TEMP DEBUG (Gilroy Froala investigation) ===
                pres_lists = main_content.find_all("ul", attrs={"role": "presentation"})
                print(f"[DEBUG] Found {len(pres_lists)} <ul role='presentation'> inside main_content")

                for i, ul in enumerate(pres_lists[:6]):  # limit noise
                    txt = ul.get_text(" ", strip=True)[:220]
                    print(f"[DEBUG]   pres_list[{i}] inner text: {txt}")

                # Look for any <strong> containing "available" or "units" anywhere in main_content
                strongs = main_content.find_all("strong")
                interesting_strongs = [s.get_text(" ", strip=True) for s in strongs if "available" in s.get_text().lower() or "units" in s.get_text().lower()]
                print(f"[DEBUG] Strong tags with 'available' or 'units': {interesting_strongs[:5]}")
                # === END TEMP DEBUG ===

                # Handle Froala-generated lists (one <ul role="presentation"> per item)
                records.extend(_parse_froala_availability_blocks(main_content, authority))

            # Look for availability-related links (property flyers, AMI flyers, etc.)
            # Try to associate them with a nearby <strong> (property name) when possible
            for a in soup.find_all("a", href=True):
                link_text = a.get_text(" ", strip=True)
                link_lower = link_text.lower()
                href_lower = a.get("href", "").lower()

                is_flyer = ("flyer" in link_lower or 
                            "official flyer" in link_lower or 
                            "available" in link_lower or
                            "50%" in link_text or "60%" in link_text or
                            "ami" in link_lower)

                if is_flyer:
                    # Try to find a nearby <strong> (often the property name)
                    # Walk up a couple levels if needed
                    parent = a.parent
                    nearby_strong = None
                    search_node = parent
                    for _ in range(3):  # walk up a few levels
                        if search_node:
                            nearby_strong = search_node.find("strong")
                            if nearby_strong:
                                break
                            search_node = getattr(search_node, 'parent', None)

                    context_name = nearby_strong.get_text(strip=True) if nearby_strong else ""
                    parent = a.parent
                    nearby_context = parent.get_text(" ", strip=True)[:200] if parent else ""

                    records.append({
                        "source_url": source,
                        "authority": authority,
                        "extraction_method": "flyer_link",
                        "property_name": context_name,
                        "flyer_text": link_text,
                        "flyer_url": a.get("href", ""),
                        "nearby_context": nearby_context,
                        "last_seen": _dt.now().isoformat(),
                        "first_seen": _dt.now().isoformat(),
                        "source": f"cdn:{authority.lower().replace(' ', '_').replace('.', '')}",
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

    # Diagnostic: show any housing-related JSON we intercepted during the source page load
    if 'captured_json' in locals() and captured_json:
        print(f"[DEBUG] Intercepted {len(captured_json)} housing-related JSON responses:")
        for item in captured_json[:5]:
            print(f"    {item['url']}")
            if isinstance(item.get('data'), dict):
                keys = list(item['data'].keys())[:8]
                print(f"      keys: {keys}")
            elif isinstance(item.get('data'), list):
                print(f"      list of {len(item['data'])} items")
    elif 'captured_json' in locals():
        print("[DEBUG] No housing-related JSON responses intercepted on source page")

    return records
