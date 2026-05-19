"""
San José Specific Extraction Module

San José uses a modern Next.js-based affordable housing portal (housing.sanjoseca.gov).
This is a fundamentally different data flow from Gilroy-style PDF lists.

We aggressively capture JSON responses (especially anything that looks like
listings.json, opportunities, search results, or API data) and map them
richly into the shared HousingRecord shape so the rest of the pipeline
works identically to PDF extraction.
"""

from __future__ import annotations

import re
from playwright.sync_api import sync_playwright
from typing import List, Any, Optional

from housing_list_search.extraction.pdf import HousingRecord


def _looks_like_housing_item(item: dict) -> bool:
    if not isinstance(item, dict):
        return False
    keys = {str(k).lower() for k in item.keys()}

    # Reject obvious config / feature flag blobs
    if any(k.startswith("enable") or k.startswith("show") or k.startswith("limit") or k.startswith("export") for k in keys):
        return False
    if "buildId" in keys or "runtime" in keys:
        return False

    # This portal's real objects always have "name" + "id" or "status"
    if "name" in keys and ("id" in keys or "status" in keys or "listingsbuildingaddress" in keys):
        return True

    strong_signals = {"name", "title", "property", "address", "status", "application", "url", "id", "slug"}
    medium_signals = {"bedrooms", "units", "ami", "income", "manager", "contact"}
    score = len(keys & strong_signals) + 0.5 * len(keys & medium_signals)
    return score >= 1.5


def _find_listing_arrays(obj: Any, depth: int = 0, max_depth: int = 8) -> List[list]:
    """Recursively hunt for arrays that contain plausible housing opportunity objects."""
    if depth > max_depth or obj is None:
        return []
    results: List[list] = []

    if isinstance(obj, list):
        if len(obj) > 0 and all(isinstance(x, dict) for x in obj[:min(5, len(obj))]):
            if any(_looks_like_housing_item(x) for x in obj[:5]):
                results.append(obj)
        for child in obj:
            results.extend(_find_listing_arrays(child, depth + 1, max_depth))
    elif isinstance(obj, dict):
        for v in obj.values():
            results.extend(_find_listing_arrays(v, depth + 1, max_depth))
    return results


def _extract_address(item: dict) -> str:
    """Handle the portal's listingsBuildingAddress field (can be str or nested dict)."""
    addr = item.get("listingsBuildingAddress")
    if isinstance(addr, str):
        return addr.strip()
    if isinstance(addr, dict):
        parts = []
        for k in ["street", "streetAddress", "address", "city", "state", "zipCode", "zip"]:
            v = addr.get(k)
            if v:
                parts.append(str(v))
        if parts:
            return ", ".join(parts)
    # Fallbacks
    for k in ["address", "streetAddress", "fullAddress", "location"]:
        if item.get(k):
            return str(item.get(k)).strip()
    return ""


def _extract_bedrooms_from_units(item: dict) -> str:
    """Pull bedroom info from unitsSummarized, unitGroups, or buildingTotalUnits."""
    # Most common on this portal
    us = item.get("unitsSummarized")
    if isinstance(us, dict):
        # Often looks like "0 - 3 BR" or a dict with counts
        for k in ["unitTypes", "summary", "text", "description"]:
            if us.get(k):
                return str(us.get(k))
    ug = item.get("unitGroups") or item.get("units")
    if isinstance(ug, list) and ug:
        brs = []
        for u in ug[:6]:
            if isinstance(u, dict):
                b = u.get("bedrooms") or u.get("br") or u.get("unitType")
                if b:
                    brs.append(str(b))
        if brs:
            return ", ".join(sorted(set(brs), key=str))
    bt = item.get("buildingTotalUnits")
    ua = item.get("unitsAvailable")
    if bt or ua:
        return f"{ua or '?'} of {bt} units" if bt else str(ua)
    return ""


def _san_jose_record_from_item(item: dict, source_url: str) -> Optional[HousingRecord]:
    """Map one real open/closed listing from the San José API into HousingRecord."""
    if not isinstance(item, dict):
        return None

    name = (item.get("name") or "").strip()
    if not name:
        return None

    address = _extract_address(item)
    phone = (item.get("leasingAgentPhone") or "").strip()
    email = (item.get("leasingAgentEmail") or "").strip()
    manager = (item.get("leasingAgentName") or item.get("developer") or "").strip()

    status = (item.get("status") or "").strip()
    waitlist = ""
    if item.get("isWaitlistOpen"):
        waitlist = f"waitlist open ({item.get('waitlistOpenSpots', '?')} spots)"
    elif item.get("waitlistCurrentSize"):
        waitlist = f"waitlist size: {item.get('waitlistCurrentSize')}"

    bedrooms = _extract_bedrooms_from_units(item)
    community = (item.get("reservedCommunityDescription") or item.get("reservedCommunityMinAge") or "").strip()
    if item.get("reservedCommunityTypes"):
        # Sometimes a list of objects
        try:
            rct = item["reservedCommunityTypes"]
            if isinstance(rct, list) and rct:
                community = str(rct[0].get("name", community) if isinstance(rct[0], dict) else rct[0])
        except Exception:
            pass

    # Application / detail URL
    slug = item.get("urlSlug")
    if slug:
        doc_url = f"https://housing.sanjoseca.gov/listing/{item.get('id')}/{slug}"
    else:
        doc_url = item.get("resultLink") or f"https://housing.sanjoseca.gov/listing/{item.get('id')}"

    # Rich notes
    notes_parts = []
    if status:
        notes_parts.append(f"status: {status}")
    if waitlist:
        notes_parts.append(waitlist)
    due = item.get("applicationDueDate")
    if due:
        notes_parts.append(f"due: {str(due)[:10]}")
    if item.get("applicationFee"):
        notes_parts.append(f"fee: ${item.get('applicationFee')}")
    if item.get("digitalApplication"):
        notes_parts.append("digital app available")
    if item.get("paperApplication"):
        notes_parts.append("paper app accepted")

    notes = "; ".join(notes_parts) if notes_parts else f"San José listing (id: {item.get('id')})"

    # Confidence: these are authoritative records from the city portal
    confidence = "high" if (address or phone or email or bedrooms) else "medium"

    return HousingRecord(
        authority="City of San José",
        property_name=name,
        address=address,
        phone=phone,
        email=email,
        property_manager=manager,
        community_type=community,
        bedrooms=bedrooms,
        supportive_services=(item.get("servicesOffered") or "").strip(),
        notes=notes,
        document_url=doc_url,
        confidence=confidence,
    )


def extract_san_jose_listings(max_results: int = 40) -> List[HousingRecord]:
    """
    Main entry point for San José extraction.

    Actively drives the portal and captures the real JSON data feeds
    (especially the listings/opportunities endpoint). Maps every useful
    field into HousingRecord so downstream code (CSV, future DB, HTML table)
    works the same as for Gilroy PDFs.
    """
    records: List[HousingRecord] = []
    captured: List[dict] = []          # {url, data, keys_preview}
    interesting_urls: List[str] = []

    def handle_response(response):
        try:
            ct = (response.headers.get("content-type") or "").lower()
            if "json" not in ct:
                return

            url = response.url
            # Be extremely greedy on anything that smells like the data we need
            lower_url = url.lower()
            is_interesting = any(x in lower_url for x in [
                "listings", "opportunities", "search", "api/", "en/", "results",
                "affordable", "property", "application"
            ])

            text = response.text()
            if len(text) > 2_000_000:   # safety valve
                return

            data = response.json()

            preview_keys = []
            if isinstance(data, dict):
                preview_keys = list(data.keys())[:12]
            elif isinstance(data, list) and data and isinstance(data[0], dict):
                preview_keys = list(data[0].keys())[:12]

            entry = {"url": url, "data": data, "preview": preview_keys}
            captured.append(entry)

            if is_interesting:
                interesting_urls.append(url)

        except Exception:
            pass

    with sync_playwright() as p:
        browser = p.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("response", handle_response)

        # Load the main portal
        page.goto("https://housing.sanjoseca.gov/", wait_until="networkidle", timeout=120000)
        page.wait_for_timeout(3000)

        # Aggressively try to surface the rental opportunities view
        click_texts = [
            "See Rentals", "Find Apartments", "View All", "Accepting Applications",
            "Search Properties", "Rentals", "Opportunities"
        ]
        for txt in click_texts:
            try:
                page.click(f"text={txt}", timeout=2500)
                page.wait_for_timeout(4000)
            except Exception:
                continue

        # Scroll to trigger lazy-loaded data
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2500)
        except Exception:
            pass

        browser.close()

    # Diagnostic: show what the real backend actually served us
    print(f"\n[SanJosé] Captured {len(captured)} JSON responses. Interesting URLs:")
    for u in interesting_urls[:15]:
        print(f"   {u}")

    # Light diagnostic
    if any("listings.json" in u.lower() for u in interesting_urls):
        print("[SanJosé] Successfully captured live data from /en/listings.json (open + closed listings)")
    else:
        print("[SanJosé] WARNING: did not see the canonical listings.json in this run")

    # === Direct extraction from the real /en/listings.json we captured ===
    listings_json_url = None
    open_listings: List[dict] = []
    closed_listings: List[dict] = []

    for cap in captured:
        if "listings.json" in cap["url"].lower() and "/en/listings" in cap["url"].lower():
            listings_json_url = cap["url"]
            data = cap["data"]
            pp = data.get("pageProps", {}) if isinstance(data, dict) else {}
            if isinstance(pp.get("openListings"), list):
                open_listings = pp["openListings"]
            if isinstance(pp.get("closedListings"), list):
                closed_listings = pp["closedListings"]
            break

    # Combine (open first, then closed) and map with the rich San José mapper
    all_items = open_listings + closed_listings
    seen = set()
    for item in all_items:
        if not isinstance(item, dict):
            continue
        key = item.get("id") or item.get("name")
        if key in seen:
            continue
        seen.add(key)

        rec = _san_jose_record_from_item(item, listings_json_url or "https://housing.sanjoseca.gov/")
        if rec:
            records.append(rec)

        if len(records) >= max_results:
            break

    # Hard fallback (very unlikely to be needed now)
    if not records:
        for cap in captured:
            for arr in _find_listing_arrays(cap["data"]):
                for it in arr:
                    rec = _san_jose_record_from_item(it, cap["url"])
                    if rec:
                        records.append(rec)
                    if len(records) >= max_results:
                        break

    # Final dedup on (name + address)
    final: List[HousingRecord] = []
    seen2 = set()
    for r in records:
        k = (r.property_name[:60].lower(), r.address[:60].lower())
        if k not in seen2:
            seen2.add(k)
            final.append(r)

    print(f"[SanJosé] Produced {len(final)} structured HousingRecord objects.")
    return final[:max_results]


def records_to_markdown(records: List[HousingRecord]) -> str:
    """Produce a human-readable markdown table (same spirit as discovery output)."""
    if not records:
        return "_No records extracted._"

    lines = ["| Property | Address | Bedrooms | Status / Notes | Apply / Contact |",
             "|----------|---------|----------|----------------|-----------------|"]
    for r in records:
        name = (r.property_name or "(unnamed)")[:55]
        addr = (r.address or "")[:45]
        br = (r.bedrooms or "")[:18]
        notes = (r.notes or "")[:55].replace("|", " ")
        link = r.document_url or ""
        if link and len(link) > 55:
            link = link[:52] + "..."
        lines.append(f"| {name} | {addr} | {br} | {notes} | {link} |")
    return "\n".join(lines)
