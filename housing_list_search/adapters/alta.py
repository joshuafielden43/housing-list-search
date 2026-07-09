"""
Alta Housing Adapter — First-Class (Company-Named)

Alta Housing (altahousing.org) is a long-standing third-party administrator
for Below Market Rate (BMR) programs, most notably for the City of Palo Alto
(and potentially other jurisdictions over time).

This is the reference adapter for the "Alta Housing" delegated administrator pattern.

Core responsibilities:
- Centralize access to waiting lists, interest lists, and application processes.
- Extract and normalize eligibility criteria (income minimums and maximums,
  AMI tiers, local preferences, asset limits, etc.).
- Surface property-level information where published (including any maps or lists).
- Support the freshness / delta system by returning records with proper
  `last_seen`, `source`, and `source_url` metadata.

Design goals:
- Treat Alta as a stable, recurring company pattern (not a one-city hack).
- Produce structured, actionable data that can be used directly by nonprofits
  without requiring them to hunt across multiple sites.
- Be resilient to the common shapes these programs take (waiting lists,
  periodic lotteries, property-specific waitlists, published PDFs/maps).

Scope & Guardrails
------------------
In Scope:
- Extracting waiting list / interest list signup links and status.
- Parsing eligibility rules and criteria from official pages and documents.
- Capturing property lists, maps, and contact information when available.
- Returning records with freshness metadata for delta processing.
- Handling both ownership (BMR purchase) and rental paths.

Out of Scope:
- Creating accounts or submitting applications on behalf of users.
- Solving CAPTCHAs or login walls (if they appear).
- Building a full real-time inventory scraper for every unit Alta touches
  (most programs do not publish this; we surface what is publicly available).

Known Low-Value Patterns
------------------------
- Pages that only contain high-level program descriptions with no actionable
  links or criteria.
- Marketing copy without links to actual waitlists or applications.
- Outdated PDFs that have not been updated in years (we still extract but
  mark source freshness clearly).

PATTERN FOR NEW ONE-OFF ADAPTERS (Alta-Style Administrators)
------------------------------------------------------------
When another city or jurisdiction is found to use Alta Housing (or a similar
long-term contracted nonprofit administrator):

1. Add the city to TARGETS.md with the clean public URL(s) and `alta` in the
   scraping measures.
2. Call `scrape_alta(authority, url)` (or the generalized entry point).
3. The adapter should return records containing at minimum:
   - program_type (ownership / rental / both)
   - waitlist_or_interest_link
   - eligibility (structured where possible: min_income, max_income, ami_tiers, preferences)
   - contacts and administrator info
   - source metadata for freshness tracking

This prevents us from re-inventing the wheel every time a new city using the
same administrator appears.

All listings matter — we extract both classic BMR units and any subsidized or
special-needs housing that the administrator surfaces.
"""

from __future__ import annotations

import logging
from datetime import datetime as _dt
from typing import Any

from bs4 import BeautifulSoup
from playwright.sync_api import TimeoutError as PlaywrightTimeout
from playwright.sync_api import sync_playwright

from housing_list_search.playwright_nav import safe_goto
from housing_list_search.scraper import polite_get

logger = logging.getLogger(__name__)

# Alta's own property directory — a static WordPress grid of .prop-box cards,
# each carrying name, full address, waitlist status, and a detail-page link.
# This is the per-property availability signal (theunitedeffort.org watches
# these same pages for staleness).
PROPERTY_DIRECTORY_URL = "https://altahousing.org/current-properties/"

# .prop-flag badge text → our listing_status vocabulary (status_labels.py)
_STATUS_MAP = {
    "waitlist open": "waitlist",
    "waitlist closed": "closed",
    "available": "open",
    "now leasing": "open",
    "coming soon": "coming_soon",
}


def _jitter(seconds: float = 0.8) -> None:
    import random
    import time

    time.sleep(seconds + random.uniform(0.2, 0.9))


def _get_headers() -> dict[str, str]:
    return {
        "User-Agent": "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 "
        "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
        "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
        "Accept-Language": "en-US,en;q=0.9",
    }


def scrape_alta(authority: str, url: str, timeout: int = 60000) -> list[dict[str, Any]]:
    """
    Public entry point for the Alta Housing first-class adapter.

    Given a target URL (usually the city's BMR or housing programs page) and
    the authority name, returns structured records with eligibility, waitlist
    links, contacts, and freshness metadata.
    """
    logger.info(f"[alta] Starting scrape_alta for {authority} using {url}")

    records: list[dict[str, Any]] = []
    now_iso = _dt.now().isoformat()
    source_base = f"alta:{authority.lower().replace(' ', '_')}"

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        context = browser.new_context(extra_http_headers=_get_headers())
        page = context.new_page()

        try:
            _jitter(0.6)
            safe_goto(page, url, wait_until="domcontentloaded", timeout=timeout)
            _jitter(1.0)

            # Try to wait for network to settle
            try:
                page.wait_for_load_state("networkidle", timeout=15000)
            except PlaywrightTimeout:
                pass

            content = page.content()
            soup = BeautifulSoup(content, "html.parser")

            # --- Ownership / Purchase Program ---
            ownership_info = _extract_ownership_section(soup, page)
            if ownership_info:
                rec = {
                    "authority": authority,
                    "program_type": "ownership",
                    "property_name": f"{authority} BMR Ownership (Alta Housing)",
                    "url": ownership_info.get("waitlist_link") or url,
                    "status": ownership_info.get("status", "See administrator"),
                    "notes": ownership_info.get("notes", ""),
                    "eligibility": ownership_info.get("eligibility", ""),
                    "administrator": "Alta Housing",
                    "administrator_url": "https://altahousing.org/",
                    "administrator_contact": ownership_info.get("contact", ""),
                    "confidence": 0.85,
                    "last_seen": now_iso,
                    "first_seen": now_iso,
                    "source": f"{source_base}:ownership",
                    "source_url": url,
                    "expires_at": "",
                }
                records.append(rec)

            # --- Rental Program ---
            rental_info = _extract_rental_section(soup, page)
            if rental_info:
                rec = {
                    "authority": authority,
                    "program_type": "rental",
                    "property_name": f"{authority} BMR Rentals (via Alta Housing)",
                    "url": rental_info.get("main_link") or url,
                    "status": "Contact individual properties",
                    "notes": rental_info.get("notes", ""),
                    "eligibility": rental_info.get("eligibility", ""),
                    "administrator": "Alta Housing",
                    "administrator_url": "https://altahousing.org/",
                    "administrator_contact": rental_info.get("contact", ""),
                    "confidence": 0.75,
                    "last_seen": now_iso,
                    "first_seen": now_iso,
                    "source": f"{source_base}:rental",
                    "source_url": url,
                    "expires_at": "",
                }
                records.append(rec)

            # --- Map / Portfolio Link ---
            map_link = _find_affordable_map(soup)
            if map_link:
                rec = {
                    "authority": authority,
                    "program_type": "portfolio_map",
                    "property_name": f"{authority} Affordable Housing Map",
                    "url": map_link,
                    "status": "Interactive map",
                    "notes": "Shows existing BMR and other restricted properties.",
                    "administrator": "Alta Housing",
                    "administrator_url": "https://altahousing.org/",
                    "confidence": 0.9,
                    "last_seen": now_iso,
                    "first_seen": now_iso,
                    "source": f"{source_base}:map",
                    "source_url": map_link,
                    "expires_at": "",
                }
                records.append(rec)

        except Exception as e:
            logger.exception(f"[alta] Error scraping {authority}: {e}")
        finally:
            browser.close()

    # Per-property availability from Alta's own directory (single static fetch;
    # polite_get applies the rate-limit delay).
    records.extend(scrape_property_directory(authority))

    logger.info(f"[alta] Extracted {len(records)} records for {authority}")
    return records


def scrape_property_directory(authority: str = "") -> list[dict[str, Any]]:
    """Parse altahousing.org/current-properties/ into per-property records.

    Each .prop-box card yields property name, full street address (with city),
    waitlist status, and the property detail URL. The directory spans every
    city Alta operates in (Palo Alto, Mountain View, Redwood City), so the
    city lives in the address while `authority` records which TARGETS.md row
    triggered the scrape.
    """
    from housing_list_search.scraper import require_response

    resp = require_response(
        polite_get(PROPERTY_DIRECTORY_URL),
        PROPERTY_DIRECTORY_URL,
        context="alta",
    )

    soup = BeautifulSoup(resp.text, "html.parser")
    now_iso = _dt.now().isoformat()
    records: list[dict[str, Any]] = []
    seen_urls: set[str] = set()

    for box in soup.find_all("div", class_="prop-box"):
        title_link = box.select_one(".prop-title a") or box.find("a", href=True)
        if not title_link:
            continue
        name = title_link.get_text(strip=True)
        detail_url = title_link.get("href", "")
        if not name or detail_url in seen_urls:
            continue
        seen_urls.add(detail_url)

        flag = box.select_one(".prop-flag")
        status_text = flag.get_text(strip=True) if flag else ""
        listing_status = _STATUS_MAP.get(status_text.lower(), "")

        desc = box.select_one(".block-desc")
        address = desc.get_text(" ", strip=True) if desc else ""

        records.append(
            {
                "authority": authority or "Alta Housing",
                "property_name": name,
                "address": address,
                "url": detail_url,
                "status": status_text or "See property page",
                "listing_status": listing_status,
                "administrator": "Alta Housing",
                "administrator_url": "https://altahousing.org/",
                "administrator_phone": "(650) 321-9709",
                "notes": f"Alta Housing managed property | directory status: {status_text}",
                "confidence": "high",
                "last_seen": now_iso,
                "first_seen": now_iso,
                "source": "alta:property_directory",
                "source_url": PROPERTY_DIRECTORY_URL,
                "expires_at": "",
            }
        )

    logger.info("[alta] Property directory: %d properties", len(records))
    return records


def _extract_ownership_section(soup: BeautifulSoup, page) -> dict[str, Any]:
    """Look for waiting list, purchase program, and eligibility info."""
    info: dict[str, Any] = {}

    text = soup.get_text(" ", strip=True).lower()

    # Look for waiting list / interest list language
    if "waiting list" in text or "interest list" in text:
        for a in soup.find_all("a", href=True):
            href = a["href"]
            link_text = a.get_text(strip=True).lower()
            if any(kw in link_text for kw in ["waitlist", "interest", "apply", "sign up"]):
                if "http" not in href:
                    base = page.url.split("/")[0:3]
                    href = (
                        "/".join(base) + href
                        if href.startswith("/")
                        else page.url.rsplit("/", 1)[0] + "/" + href
                    )
                info["waitlist_link"] = href
                break

    # Basic status detection
    if "currently closed" in text or "waiting list is closed" in text:
        info["status"] = "Waiting list closed"
    elif "open" in text and "waitlist" in text:
        info["status"] = "Waiting list open or accepting updates"

    # Try to pull eligibility text
    for elem in soup.find_all(["p", "li", "div"]):
        t = elem.get_text(" ", strip=True)
        if "income" in t.lower() and len(t) > 40 and len(t) < 600:
            info["eligibility"] = t[:500]
            break

    # Contact info
    for a in soup.find_all("a", href=True):
        if "@" in a.get_text() or "altahousing" in a["href"].lower():
            info["contact"] = a.get_text(strip=True) or a["href"]
            break

    return info


def _extract_rental_section(soup: BeautifulSoup, page) -> dict[str, Any]:
    """Look for rental program information and contacts."""
    info: dict[str, Any] = {}

    text = soup.get_text(" ", strip=True).lower()
    if "rental" in text:
        info["notes"] = "Rental BMR units managed through individual property managers."

    for a in soup.find_all("a", href=True):
        href = a["href"].lower()
        if "rental" in href or "rentals" in a.get_text().lower():
            base = page.url.split("/")[0:3]
            full = "/".join(base) + a["href"] if a["href"].startswith("/") else a["href"]
            info["main_link"] = full
            break

    return info


def _find_affordable_map(soup: BeautifulSoup) -> str | None:
    for a in soup.find_all("a", href=True):
        text = a.get_text().lower()
        href = a["href"].lower()
        if "map" in text or "map" in href:
            if "affordable" in text or "housing" in text:
                return a["href"]
    return None
