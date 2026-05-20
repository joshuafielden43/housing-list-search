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

A common variant (seen on Gilroy and similar Housing Group cities) is that the
authoritative current availability list lives on a dedicated sub-page
(e.g. "/797/Affordable-Apartments" titled "List of Affordable Rentals"). That page
contains the human-visible "Property Name - N available units" blocks with
contact info and per-property "Official Flyer" links (including separate 50%AMI
and 60%AMI variants) pointing to DocumentCenter/View/XXXX. The adapter now
follows to these list pages, harvests the specific flyer links with surrounding
property context, processes the real PDFs, and produces named records using
both list text and URL slugs.
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

    # If the source itself is a DocumentCenter View link (common for Gilroy property flyers), treat it directly
    is_direct_documentcenter = False
    if "/DocumentCenter/View/" in source:
        document_urls.append(source)
        is_direct_documentcenter = True
        logger.info(f"[cdn] Source is a direct DocumentCenter View link — will process for context + PDF")

    # For Gilroy (and similar hard DocumentCenter sites), seed a small set of known high-value property availability flyer IDs.
    # These are the actual "Official Flyer 50%/60% AMI" documents that contain the real unit data the human sees referenced on the landing page.
    # This is the pragmatic solution for pages where the nice list is not machine-readable.
    if "gilroy" in source.lower():
        known_gilroy_property_flyers = [
            "https://www.cityofgilroy.org/DocumentCenter/View/16932",  # Wheeler Manor example
            # Add more active property flyer IDs here as they are identified (the ones with real availability data)
        ]
        for f in known_gilroy_property_flyers:
            if f not in document_urls:
                document_urls.append(f)
                logger.info(f"[cdn] Seeded known Gilroy property flyer: {f}")

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
            locale="en-US",
            timezone_id="America/Los_Angeles",
            geolocation={"latitude": 37.0058, "longitude": -121.5683},  # Gilroy, CA
            permissions=["geolocation"],
        )

        page = context.new_page()

        # === DEEP JS-LEVEL NETWORK SPY (for hard JS-rendered content like Gilroy availability) ===
        js_requests = []

        # Inject hooks as early as possible
        page.add_init_script("""
            (() => {
                const log = (type, url, extra = '') => {
                    // Send to Playwright console so we can capture it
                    console.log(`[JS-NET ${type}] ${url} ${extra}`);
                };

                // Hook fetch
                const origFetch = window.fetch;
                window.fetch = function(...args) {
                    const url = (typeof args[0] === 'string') ? args[0] : (args[0]?.url || '');
                    log('fetch', url);
                    return origFetch.apply(this, args);
                };

                // Hook XMLHttpRequest
                const origOpen = XMLHttpRequest.prototype.open;
                XMLHttpRequest.prototype.open = function(method, url, ...rest) {
                    log('xhr', url);
                    return origOpen.apply(this, arguments);
                };

                // Hook send too (some XHR set headers after open)
                const origSend = XMLHttpRequest.prototype.send;
                XMLHttpRequest.prototype.send = function(body) {
                    return origSend.apply(this, arguments);
                };
            })();
        """)

        # Capture console messages from the JS hooks
        def on_console(msg):
            text = msg.text
            if '[JS-NET' in text or 'housing' in text.lower() or 'available' in text.lower() or 'units' in text.lower():
                js_requests.append(text)

        page.on("console", on_console)

        # Also keep the existing response listener (complementary)
        all_responses = []
        captured_json = []

        def _on_response(response):
            try:
                ct = response.headers.get("content-type", "").lower()
                url = response.url
                url_lower = url.lower()
                entry = {"url": url, "status": response.status, "content_type": ct}
                all_responses.append(entry)

                if "json" in ct and any(kw in url_lower for kw in ["housing", "available", "units", "bmr", "affordable", "property", "inventory", "list", "domain"]):
                    try:
                        data = response.json()
                        if isinstance(data, (list, dict)):
                            captured_json.append({"url": url, "data": data})
                    except Exception:
                        pass

                # Special capture for docaccess domain config (seen on Gilroy)
                if "docaccess.com" in url_lower and "domain.json" in url_lower:
                    try:
                        data = response.json()
                        print(f"[DOCACCESS DOMAIN] {url}")
                        print(f"   keys: {list(data.keys()) if isinstance(data, dict) else 'list'}")
                        if isinstance(data, dict) and "available" in str(data).lower():
                            print("   !!! Contains 'available' data")
                    except Exception:
                        pass
            except Exception:
                pass

        page.on("response", _on_response)
        # === END DEEP JS-LEVEL NETWORK SPY ===
        # === END AGGRESSIVE DIAGNOSTIC CAPTURE ===

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

            # Special case for direct DocumentCenter View source (property flyers)
            if is_direct_documentcenter:
                logger.info("[cdn] Direct DocumentCenter source — forcing document processing path")
                # The rest of the function will pick it up via the document_urls list we seeded earlier

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

            # (Gilroy-specific diagnostics removed — data lives in DocumentCenter PDFs)

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
                    "publisheddocument", "rental", "housing", "showdocument",
                    "documentcenter"
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

            # === Aggressive DocumentCenter discovery (for Gilroy and similar Housing Group sites) ===
            # Many real property availability flyers are linked via older DocumentCenter View IDs
            # that don't have obvious "flyer" text next to them in the rendered HTML.
            # We prioritize ones that look like property/AMI flyers.
            try:
                raw = page.content()
                dc_matches = re.findall(r'(/DocumentCenter/View/\d+)', raw)
                # Sort so that slugs with property names or AMI indicators come first
                def score(link):
                    l = link.lower()
                    score = 0
                    if any(x in l for x in ["50ami", "60ami", "50%", "60%", "ami"]): score += 10
                    if any(x in l for x in ["wheeler", "cannery", "manor", "apartments", "village", "gardens", "court"]): score += 8
                    if "flyer" in l: score += 5
                    return -score  # higher score first

                sorted_dc = sorted(set(dc_matches), key=score)
                for m in sorted_dc:
                    full = m if m.startswith("http") else "https://www.cityofgilroy.org" + m if "cityofgilroy" in source.lower() else m
                    if full not in document_urls:
                        document_urls.append(full)
                        logger.info(f"[cdn] Found extra DocumentCenter link: {full}")
            except Exception:
                pass
            # === End aggressive DocumentCenter discovery ===

            # === Dedicated harvesting for Gilroy availability list page ===
            # The authoritative current list with per-property "Official Flyer 50%/60%AMI" links
            # lives on the "List of Affordable Rentals in Gilroy" page. We look for it and harvest
            # the exact (property_name, specific DocumentCenter flyer link) pairs from the list text.
            try:
                if "gilroy" in source.lower():
                    page_text = page.inner_text("body").lower() if hasattr(page, "inner_text") else ""
                    if "available units" in page_text and "official flyer" in page_text:
                        # Exhaustive harvest: every DocumentCenter "Official Flyer" link on the page gets the nearest
                        # preceding "Property Name - N available units" text as its property_name.
                        all_flyer_links = []
                        for a in soup.find_all("a", href=True):
                            href = a.get("href", "")
                            txt = a.get_text(strip=True).lower()
                            if "documentcenter/view" in href.lower() and ("official flyer" in txt or "50%ami" in txt or "60%ami" in txt):
                                full = href if href.startswith("http") else "https://www.cityofgilroy.org" + href if href.startswith("/") else href
                                all_flyer_links.append((a, full))

                        for a, full in all_flyer_links:
                            # Walk up parents to find the nearest "X available units" text
                            prop_name = ""
                            node = a
                            for _ in range(8):
                                if node is None: break
                                txt = node.get_text(" ", strip=True)
                                m = re.search(r"([A-Z][A-Za-z][A-Za-z ]+?)\s*-\s*\d+\s*available", txt, re.IGNORECASE)
                                if m:
                                    prop_name = m.group(1).strip()
                                    break
                                node = getattr(node, "parent", None)

                            if full not in document_urls:
                                document_urls.append(full)

                        # Raw HTML fallback (very robust)
                        raw_html = page.content()
                        for m in re.finditer(r'/DocumentCenter/View/(\d+)', raw_html):
                            dc_id = m.group(1)
                            full = f"https://www.cityofgilroy.org/DocumentCenter/View/{dc_id}"
                            idx = m.start()
                            preceding = raw_html[max(0, idx-900):idx]
                            prop_name = ""
                            m2 = re.search(r"([A-Z][A-Za-z][A-Za-z ]+?)\s*-\s*\d+\s*available", preceding, re.IGNORECASE)
                            if m2:
                                prop_name = m2.group(1).strip()
                            if full not in document_urls:
                                document_urls.append(full)
            except Exception:
                pass
            # === End dedicated harvesting ===

            # === Follow "availability list" sub-pages on Gilroy-style sites ===
            # The real current per-property "Official Flyer 50%/60%AMI" links live on pages like
            # /797/Affordable-Apartments (the "List of Affordable Rentals in Gilroy - Updated July 2025" section).
            # We look for links or text indicating the current availability list and pull the
            # specific "Official Flyer" links with their surrounding property context.
            try:
                if "gilroy" in source.lower() or "affordable" in source.lower():
                    # Look for the dedicated availability list page
                    avail_page_links = re.findall(r'href=["\']([^"\']*(?:797|affordable-apartments|list-of-affordable)[^"\']*)["\']', raw, re.I)
                    for link in set(avail_page_links):
                        if "documentcenter" not in link.lower():
                            full = link if link.startswith("http") else "https://www.cityofgilroy.org" + link if link.startswith("/") else None
                            if full and full not in document_urls:
                                document_urls.append(full)
                                logger.info(f"[cdn] Found availability list sub-page: {full}")

                    # Also directly harvest the specific per-property Official Flyer links that appear on the availability list page
                    # (these have the exact DocumentCenter IDs for the current units, with 50%/60% AMI variants)
                    # Pattern seen: links with text "Official Flyer 50%AMI" etc. next to property names
                    flyer_pattern = r'href=["\']([^"\']*DocumentCenter/View/\d+[^"\']*)["\'][^>]*>([^<]*Official Flyer[^<]*)<'
                    for m in re.finditer(flyer_pattern, raw, re.IGNORECASE):
                        full = m.group(1)
                        if not full.startswith("http"):
                            full = "https://www.cityofgilroy.org" + full if full.startswith("/") else full
                        if full not in document_urls:
                            document_urls.append(full)
                            logger.info(f"[cdn] Found per-property Official Flyer from list page: {full} ({m.group(2).strip()})")
            except Exception:
                pass
            # === End availability list sub-page following ===

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

                for u in document_urls[:max_documents * 3]:
                    u_lower = u.lower()
                    if "documentcenter/view/" in u_lower:
                        if not u_lower.endswith('.pdf'):
                            html_doc_urls.append(u)
                        else:
                            pdf_urls.append(u)
                    elif u_lower.endswith('.pdf'):
                        pdf_urls.append(u)
                    else:
                        html_doc_urls.append(u)

                # For Gilroy-style sites, strongly prefer processing DocumentCenter links that look like property/AMI flyers
                if any("gilroy" in u.lower() or "documentcenter" in u.lower() for u in document_urls):
                    def is_property_flyer(link):
                        l = link.lower()
                        return any(x in l for x in ["50ami", "60ami", "50%", "60%", "ami", "wheeler", "cannery", "manor", "apartments"]) or "flyer" in l
                    pdf_urls.sort(key=lambda x: 0 if is_property_flyer(x) else 1)
                    html_doc_urls.sort(key=lambda x: 0 if is_property_flyer(x) else 1)

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

                        # === DocumentCenter viewer context (Gilroy property flyers) ===
                        viewer_title = ""
                        viewer_desc = ""
                        if "documentcenter" in doc_url.lower():
                            h = doc_soup.find(["h1", "h2", "h3"])
                            if h:
                                viewer_title = h.get_text(strip=True)
                            if not viewer_title:
                                title_tag = doc_soup.find("title")
                                if title_tag:
                                    viewer_title = title_tag.get_text(strip=True)

                            main = doc_soup.find("div", class_="document-details") or doc_soup.find("main") or doc_soup
                            viewer_desc = main.get_text(" ", strip=True)[:300] if main else ""

                            if viewer_title:
                                logger.info(f"[cdn] DocumentCenter viewer title: {viewer_title}")

                        # Existing table extraction
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

                        # If we got a good title from a DocumentCenter View page, record it as context
                        if viewer_title and "documentcenter" in doc_url.lower():
                            records.append({
                                "source_url": doc_url,
                                "authority": authority,
                                "extraction_method": "documentcenter_viewer",
                                "property_name": viewer_title,
                                "viewer_description": viewer_desc[:200],
                                "last_seen": _dt.now().isoformat(),
                                "first_seen": _dt.now().isoformat(),
                                "source": f"cdn:{authority.lower().replace(' ', '_').replace('.', '')}",
                            })

                        # Special handling for Gilroy-style "List of Affordable Rentals" pages
                        # The page has blocks like:
                        #   "The Cannery Apartments - 5 available units"
                        #   Email / Phone
                        #   Official Flyer (link to DocumentCenter)
                        #   Official Flyer 50%AMI (another link)
                        # We want to capture each (property_name, specific flyer link) pair.
                        has_flyer_links = any("official flyer" in a.get_text().lower() for a in doc_soup.find_all("a", href=True))
                        looks_like_availability_list = "affordable" in doc_url.lower() or "797" in doc_url or "list of affordable" in doc_url.lower()
                        if has_flyer_links and looks_like_availability_list:
                            print(f"[DEBUG-AVAIL-LIST] Walking availability list page for doc_url={doc_url}, found has_flyer_links=True, looks_like_availability_list={looks_like_availability_list}")
                            dc_count = len([a for a in doc_soup.find_all("a", href=True) if "documentcenter/view" in a.get("href","").lower()])
                            print(f"[DEBUG-AVAIL-LIST] Total DocumentCenter links on page: {dc_count}")
                            # Find every link that is clearly one of the per-property Official Flyers
                            for a in doc_soup.find_all("a", href=True):
                                href = a.get("href", "")
                                link_text = a.get_text(strip=True).lower()
                                if "documentcenter/view" not in href.lower():
                                    continue
                                if not any(kw in link_text for kw in ["official flyer", "50%ami", "60%ami", "50ami", "60ami"]):
                                    # Also catch plain "Official Flyer" links that sit next to property names
                                    if "official flyer" not in link_text:
                                        continue

                                full = href if href.startswith("http") else "https://www.cityofgilroy.org" + href if href.startswith("/") else href

                                # Walk up to find the property name (look for the nearest "X available units" block)
                                prop_name = ""
                                node = a
                                for _ in range(6):
                                    if node is None:
                                        break
                                    txt = node.get_text(" ", strip=True)
                                    m = re.search(r"([A-Z][A-Za-z][A-Za-z ]+?)\s*-\s*\d+\s*available", txt, re.IGNORECASE)
                                    if m:
                                        prop_name = m.group(1).strip()
                                        break
                                    node = getattr(node, "parent", None)

                                # Fallback: take the first strong-looking name before the link in the parent
                                if not prop_name:
                                    parent = a.find_parent(["li", "p", "div"])
                                    if parent:
                                        txt = parent.get_text(" ", strip=True)
                                        m = re.search(r"([A-Z][A-Za-z][A-Za-z ]+?)\s*(?:-|–)\s*\d", txt)
                                        if m:
                                            prop_name = m.group(1).strip()

                                records.append({
                                    "source_url": doc_url,
                                    "authority": authority,
                                    "extraction_method": "availability_list_flyer",
                                    "property_name": prop_name,
                                    "flyer_text": a.get_text(strip=True),
                                    "flyer_url": full,
                                    "last_seen": _dt.now().isoformat(),
                                    "first_seen": _dt.now().isoformat(),
                                    "source": f"cdn:{authority.lower().replace(' ', '_').replace('.', '')}",
                                })

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
                            for rec in pdf_recs:
                                # rec is a HousingRecord dataclass — convert to dict for uniformity
                                r = rec.__dict__ if hasattr(rec, "__dict__") else rec
                                r = dict(r) if not isinstance(r, dict) else r
                                r.setdefault("last_seen", _dt.now().isoformat())
                                r.setdefault("first_seen", _dt.now().isoformat())
                                r.setdefault("source", f"cdn:{authority.lower().replace(' ', '_').replace('.', '')}")
                                r.setdefault("source_url", pdf_url)

                                # Attach good name from DocumentCenter slug if the parser gave a weak one
                                if "documentcenter" in pdf_url.lower() and (not r.get("property_name") or len(str(r.get("property_name",""))) < 5):
                                    m = re.search(r"/DocumentCenter/View/\d+/([A-Za-z0-9_-]+)", pdf_url)
                                    if m:
                                        slug = m.group(1).replace("-", " ").replace("_", " ")
                                        slug = re.sub(r'\s*(Flyer|Event|Calendar|50AMI|60AMI)\s*', ' ', slug, flags=re.I).strip()
                                        slug = re.sub(r'\s*(\d+)(ami|%?\s*ami)\s*$', r' (\1% AMI)', slug, flags=re.I).strip()
                                        if slug:
                                            r["property_name"] = slug

                                records.append(r)
                        except Exception as e:
                            logger.warning(f"[cdn] Failed to extract PDF {pdf_url}: {e}")

                # Merge context from availability_list_flyer records (property names from the list page)
                # into the final PDF-derived records.
                flyer_context = {}
                for r in records:
                    if r.get("extraction_method") == "availability_list_flyer" and r.get("flyer_url") and r.get("property_name"):
                        flyer_context[r["flyer_url"]] = r["property_name"]

                for r in records:
                    if r.get("source_url") in flyer_context:
                        if not r.get("property_name") or len(str(r.get("property_name", ""))) < 5:
                            r["property_name"] = flyer_context[r["source_url"]]

            # Fallback: focus on the main content area (fr-view for Froala sites like Gilroy)
            if len(records) < 5:
                logger.info("[cdn] Falling back to raw text extraction")

                main_content = soup.find("div", class_="fr-view") or soup

                # Handle Froala-generated lists (one <ul role="presentation"> per item) if present
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
                            "ami" in link_lower or
                            "50ami" in href_lower or "60ami" in href_lower)

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

                    # For DocumentCenter links, the slug in the URL is often the best signal
                    # e.g. /DocumentCenter/View/16932/Wheeler-Manor-Flyer_50AMI
                    if not context_name and "documentcenter" in href_lower:
                        slug_match = re.search(r"/DocumentCenter/View/\d+/([A-Za-z0-9_-]+)", a.get("href", ""))
                        if slug_match:
                            slug = slug_match.group(1).replace("-", " ").replace("_", " ")
                            # Turn "Wheeler Manor Flyer 50AMI" into "Wheeler Manor (50% AMI)"
                            context_name = re.sub(r'\s*(Flyer|Event|Calendar)\s*', ' ', slug, flags=re.I).strip()
                            context_name = re.sub(r'\s*(\d+)(ami|%?\s*ami)\s*$', r' (\1% AMI)', context_name, flags=re.I).strip()

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

    # === AGGRESSIVE NETWORK DIAGNOSTIC REPORT (FULL) ===
    if 'all_responses' in locals() and all_responses:
        print(f"\n[NETWORK] Total responses seen during page load: {len(all_responses)}")

        from collections import Counter
        cts = Counter(r.get("content_type", "").split(";")[0].strip() for r in all_responses if r.get("content_type"))
        print("[NETWORK] Content types seen:")
        for ct, count in cts.most_common(15):
            print(f"   {count:3d}  {ct}")

        print(f"\n[NETWORK] ALL response URLs (in order):")
        for i, r in enumerate(all_responses):
            print(f"   [{i:02d}] {r['status']:3d}  {r.get('content_type','')[:35]:<35}  {r['url']}")
    # === END AGGRESSIVE NETWORK DIAGNOSTIC REPORT ===

    # === JS-LEVEL REQUESTS (the ones the browser actually made for content) ===
    if 'js_requests' in locals() and js_requests:
        print(f"\n[JS-NET] JavaScript-level requests captured: {len(js_requests)}")
        for req in js_requests[:30]:
            print(f"   {req}")
    else:
        print("\n[JS-NET] No additional JS-level requests logged (or hooks didn't fire)")
    # === END JS-LEVEL REQUESTS ===

    # Old targeted JSON diagnostic (kept for compatibility)
    if 'captured_json' in locals() and captured_json:
        print(f"\n[DEBUG] Intercepted {len(captured_json)} housing-related JSON responses (targeted filter):")
        for item in captured_json[:5]:
            print(f"    {item['url']}")
    elif 'captured_json' in locals():
        print("\n[DEBUG] No housing-related JSON responses intercepted on source page (targeted filter)")

    return records
