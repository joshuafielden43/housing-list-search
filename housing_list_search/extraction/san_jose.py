"""
San José Affordable Housing Portal Extractor
housing.sanjoseca.gov  —  Next.js / Bloom Housing platform

=== ARCHITECTURE: WHY THIS IS NOT A PLAYWRIGHT SCRAPER ===

The portal at housing.sanjoseca.gov is a Next.js app built on the open-source
Bloom Housing platform (github.com/bloom-housing/bloom). It appears to be a
JavaScript-heavy SPA, but the /listings route uses Next.js Server-Side Rendering
(SSR): the server embeds ALL listing data into the page HTML before it reaches
the browser, inside a <script id="__NEXT_DATA__" type="application/json"> tag.

This means polite_get("/listings") returns a 1.3 MB HTML file containing every
open and closed listing as structured JSON — no browser execution required.

Confirmed working as of 2026-06-05: 46 openListings + 48 closedListings.

=== WHY /listings, NOT / ===

The homepage (/) is a pure SPA shell. polite_get("/") returns:
    <div id="__next"></div>
...with __NEXT_DATA__ that has empty pageProps (no listing data). The homepage
relies entirely on client-side JavaScript to populate the UI. Fetching / and
trying to parse listings from it will always yield zero results.

The /listings route is SSR (getServerSideProps), so it ships the full dataset
in the initial HTML response. Always target /listings.

=== WHY NOT /api/listings ===

There is no public REST API. /api/listings returns HTTP 404. The Bloom Housing
backend API is internal; the only public surface for listing data is the SSR
page props embedded in /listings HTML.

=== HOW TO FIND THE DATA IN __NEXT_DATA__ ===

Standard Next.js __NEXT_DATA__ shape:
    {
      "props": {                      <-- always present
        "pageProps": {                <-- SSR data lives here
          "openListings": [...],      <-- 46 active listings as of 2026-06-05
          "closedListings": [...],    <-- 48 closed/waitlist-only listings
          "paginationData": {...},
          "jurisdiction": {...},
          "multiselectData": {...}
        },
        "__N_SSP": true               <-- confirms this is getServerSideProps
      },
      "page": "/listings",
      "buildId": "...",               <-- changes on each Next.js deploy
      ...
    }

IMPORTANT: Do NOT read data["pageProps"] directly — that key does not exist at
the top level. The correct path is data["props"]["pageProps"]. The previous
version of this extractor had this wrong and always returned zero results from
the SSR path.

=== FIELD REFERENCE (Bloom Housing listing object) ===

Top-level fields used by this extractor:
  id                      UUID, stable identifier
  name                    Property name (always present)
  status                  "active" | "closed" | "pending"
  urlSlug                 URL-safe slug, used to construct the listing detail URL
  listingsBuildingAddress {street, city, state, zipCode, latitude, longitude}
  leasingAgentPhone       Contact phone
  leasingAgentEmail       Contact email
  leasingAgentName        Property manager / leasing contact name
  leasingAgentOfficeHours Office hours string
  developer               Management company (fallback if leasingAgentName absent)
  units[]                 Array of unit objects (see UNIT FIELDS below)
  unitsSummarized         Aggregated rent/income ranges (see UNITS SUMMARIZED)
  servicesOffered         Supportive services description
  reservedCommunityTypes  [{name: "Senior"}, ...] or null
  reservedCommunityDescription / reservedCommunityMinAge  Senior/special pop info
  isWaitlistOpen          bool
  waitlistOpenSpots       int or null
  waitlistCurrentSize     int or null
  applicationDueDate      ISO datetime string
  applicationOpenDate     ISO datetime string
  applicationFee          string (e.g. "25")
  digitalApplication      bool
  paperApplication        bool
  reviewOrderType         "firstComeFirstServe" | "lottery"
  lotteryStatus           null | string (only for lottery-type listings)
  rentalAssistance        Text describing voucher/Section 8 acceptance
  homeType                null for apartments; "singleFamily" etc. for other types
  marketingType           "marketing" (accepting apps) | "comingSoon" | etc.

UNIT FIELDS (units[] items):
  numBedrooms             int (0 = studio)
  numBathrooms            float
  monthlyRent             string ("2250")
  amiPercentage           string ("60") — percent of Area Median Income
  sqFeet                  string
  unitTypes.name          "studio" | "oneBdrm" | "twoBdrm" | "threeBdrm" | "fourBdrm"

UNITS SUMMARIZED (unitsSummarized):
  byUnitTypeAndRent[]     Array of aggregated ranges per unit type:
    unitTypes.name        Same as unit field above
    rentRange.min/max     "$2,250" / "$2,635"
    minIncomeRange        Min income required range
    areaRange             sqft range

=== PLAYWRIGHT FALLBACK ===

If __NEXT_DATA__ disappears from /listings (e.g. the city migrates to a
different Bloom Housing version that moves to client-side fetching), the
Playwright fallback below will activate. It intercepts network responses
looking for any JSON blob containing openListings/closedListings. The fallback
is intentionally kept working so that a Next.js upgrade doesn't silently break
the daily run — it just becomes slower (Playwright launch ~5s vs ~2s for a
plain HTTP fetch).

Signs that the SSR path has broken and the fallback has activated:
  - Log line: "[SanJosé] SSR path: __NEXT_DATA__ not found or empty ..."
  - Log line: "[SanJosé] Playwright fallback: captured N JSON responses"
  - Daily run becomes noticeably slower for the San José target

If the fallback also stops working, the next place to look is:
  1. The Bloom Housing API (github.com/bloom-housing/bloom) — check if the
     city has exposed a public listings endpoint in a newer API version.
  2. Network tab in Chrome DevTools on housing.sanjoseca.gov/listings — look
     for any XHR/fetch calls that return listing arrays.
  3. File a ticket with the city's IT vendor (current: Bloom Housing / HUD).

=== UPDATING THIS EXTRACTOR ===

When field names change in a Bloom Housing upgrade:
  - The _san_jose_record_from_item() function maps raw item dicts to HousingRecord.
  - Field names are unlikely to change (Bloom is open-source and stable), but
    if they do, compare the raw item keys printed in DEBUG logging against the
    field reference above and update the getter calls.
  - The _extract_address() and _extract_bedrooms_from_units() helpers isolate
    the fragile structural assumptions; update there first.
"""

from __future__ import annotations

import json
import logging
import re
from typing import Any, List, Optional

from bs4 import BeautifulSoup

from housing_list_search.extraction.pdf import HousingRecord
from housing_list_search.scraper import polite_get

logger = logging.getLogger(__name__)

# The only URL that reliably returns SSR-embedded listing data.
# Do not change to "/" — see module docstring for why.
_LISTINGS_URL = "https://housing.sanjoseca.gov/listings"

# Base URL for individual listing detail pages.
# Pattern: /listing/{uuid}/{url-slug}
_LISTING_DETAIL_BASE = "https://housing.sanjoseca.gov/listing"


# =============================================================================
# FIELD EXTRACTION HELPERS
# =============================================================================

def _extract_address(item: dict) -> str:
    """
    Extract a display address string from a listing item.

    listingsBuildingAddress is always a dict on this portal:
        {"street": "969 Meridian Ave", "city": "San Jose", "state": "CA",
         "zipCode": "95126", "latitude": 37.31, "longitude": -121.91, ...}

    street2 is present but usually null.
    """
    addr = item.get("listingsBuildingAddress")
    if isinstance(addr, dict):
        parts = []
        if addr.get("street"):
            s = addr["street"]
            if addr.get("street2"):
                s += f" {addr['street2']}"
            parts.append(s)
        if addr.get("city"):
            parts.append(addr["city"])
        if addr.get("state"):
            parts.append(addr["state"])
        if addr.get("zipCode"):
            parts.append(str(addr["zipCode"]))
        if parts:
            return ", ".join(parts)
    # Defensive fallback for schema drift
    for k in ("address", "streetAddress", "fullAddress", "location"):
        if item.get(k):
            return str(item[k]).strip()
    return ""


def _extract_bedrooms_from_units(item: dict) -> str:
    """
    Build a human-readable bedroom/AMI summary from unit data.

    The authoritative source is units[] — each element has:
        numBedrooms (int), amiPercentage (str), monthlyRent (str)

    unitsSummarized.byUnitTypeAndRent[] provides aggregated rent ranges
    when units[] is absent or empty (uncommon but seen on some listings).

    Returns strings like:
        "Studio (60% AMI), 1BR (50–60% AMI), 2BR (60% AMI)"
        "6 units available (building total: 48)"
    """
    units = item.get("units")
    if isinstance(units, list) and units:
        # Aggregate by bedroom count → set of AMI percentages
        by_br: dict[int, set] = {}
        for u in units:
            if not isinstance(u, dict):
                continue
            br = u.get("numBedrooms")
            ami = u.get("amiPercentage")
            if br is not None:
                by_br.setdefault(int(br), set())
                if ami:
                    by_br[int(br)].add(str(ami))

        if by_br:
            parts = []
            for br_count in sorted(by_br.keys()):
                label = "Studio" if br_count == 0 else f"{br_count}BR"
                amis = sorted(by_br[br_count], key=lambda x: int(x) if x.isdigit() else 0)
                if amis:
                    ami_str = "–".join(amis) if len(amis) > 1 else amis[0]
                    parts.append(f"{label} ({ami_str}% AMI)")
                else:
                    parts.append(label)
            return ", ".join(parts)

    # Fall back to unitsSummarized aggregation
    us = item.get("unitsSummarized")
    if isinstance(us, dict):
        by_type = us.get("byUnitTypeAndRent")
        if isinstance(by_type, list) and by_type:
            parts = []
            for entry in by_type:
                if not isinstance(entry, dict):
                    continue
                ut = entry.get("unitTypes") or {}
                name = ut.get("name", "") if isinstance(ut, dict) else ""
                rent = entry.get("rentRange") or {}
                lo = rent.get("min", "")
                hi = rent.get("max", "")
                rent_str = f"{lo}–{hi}".strip("–") if (lo or hi) else ""
                label = _bloom_unit_type_label(name)
                parts.append(f"{label} ({rent_str})" if rent_str else label)
            if parts:
                return ", ".join(parts)

    # Last resort: raw counts
    ua = item.get("unitsAvailable")
    bt = item.get("buildingTotalUnits")
    if ua or bt:
        return f"{ua or '?'} of {bt} units" if bt else f"{ua} available"
    return ""


def _bloom_unit_type_label(bloom_name: str) -> str:
    """
    Bloom Housing uses camelCase unit type names. Convert to human-readable.
    Known values as of Bloom v2/v3: studio, oneBdrm, twoBdrm, threeBdrm, fourBdrm.
    Future-proof: any unknown value passes through as-is.
    """
    mapping = {
        "studio": "Studio",
        "onebdrm": "1BR",
        "twobdrm": "2BR",
        "threebdrm": "3BR",
        "fourbdrm": "4BR",
    }
    return mapping.get((bloom_name or "").lower(), bloom_name or "Unknown")


def _extract_community_type(item: dict) -> str:
    """
    Pull reserved community label (Senior, Veterans, etc.).

    reservedCommunityTypes is a list of objects: [{id: ..., name: "Senior"}, ...]
    reservedCommunityDescription and reservedCommunityMinAge are scalar strings.
    """
    rct = item.get("reservedCommunityTypes")
    if isinstance(rct, list) and rct:
        names = []
        for entry in rct:
            if isinstance(entry, dict) and entry.get("name"):
                names.append(entry["name"])
            elif isinstance(entry, str):
                names.append(entry)
        if names:
            return ", ".join(names)

    desc = item.get("reservedCommunityDescription") or ""
    age = item.get("reservedCommunityMinAge") or ""
    if age:
        return f"{desc} (min age: {age})".strip()
    return desc.strip()


# =============================================================================
# RECORD MAPPER
# =============================================================================

def _san_jose_record_from_item(item: dict) -> Optional[HousingRecord]:
    """
    Map one Bloom Housing listing object → HousingRecord.

    Called for both openListings and closedListings. Closed listings are
    included because they represent real properties in the city's affordable
    housing inventory — their waitlists and contact info are still actionable
    for the nonprofit that uses this data.
    """
    if not isinstance(item, dict):
        return None

    name = (item.get("name") or "").strip()
    if not name:
        # Every real listing has a name; unnamed objects are usually metadata
        return None

    listing_id = item.get("id") or ""
    slug = item.get("urlSlug") or ""
    if slug:
        detail_url = f"{_LISTING_DETAIL_BASE}/{listing_id}/{slug}"
    elif listing_id:
        detail_url = f"{_LISTING_DETAIL_BASE}/{listing_id}"
    else:
        detail_url = _LISTINGS_URL

    address = _extract_address(item)
    phone = (item.get("leasingAgentPhone") or "").strip()
    email = (item.get("leasingAgentEmail") or "").strip()
    # developer is the management company; leasingAgentName is the contact person
    manager = (item.get("leasingAgentName") or item.get("developer") or "").strip()
    community = _extract_community_type(item)
    bedrooms = _extract_bedrooms_from_units(item)
    services = (item.get("servicesOffered") or "").strip()

    # === Build notes: pack in everything actionable ===
    notes_parts = []

    status = (item.get("status") or "").strip()
    review = (item.get("reviewOrderType") or "").strip()
    marketing = (item.get("marketingType") or "").strip()

    if status == "active" and marketing == "comingSoon":
        notes_parts.append("coming soon — not yet accepting applications")
    elif status == "active":
        notes_parts.append("accepting applications")
    elif status == "closed":
        notes_parts.append("closed — not currently accepting applications")
    elif status:
        notes_parts.append(f"status: {status}")

    if review == "lottery":
        lottery_status = (item.get("lotteryStatus") or "").strip()
        notes_parts.append(f"lottery system{': ' + lottery_status if lottery_status else ''}")
    elif review == "firstComeFirstServe":
        notes_parts.append("first-come first-served")

    if item.get("isWaitlistOpen"):
        spots = item.get("waitlistOpenSpots")
        notes_parts.append(f"waitlist open ({spots} spots)" if spots else "waitlist open")
    elif item.get("waitlistCurrentSize"):
        notes_parts.append(f"waitlist size: {item['waitlistCurrentSize']}")

    due = item.get("applicationDueDate")
    if due:
        notes_parts.append(f"due: {str(due)[:10]}")

    fee = item.get("applicationFee")
    if fee and str(fee) not in ("0", ""):
        notes_parts.append(f"app fee: ${fee}")

    app_types = []
    if item.get("digitalApplication"):
        app_types.append("online")
    if item.get("paperApplication"):
        app_types.append("paper")
    if app_types:
        notes_parts.append(f"application: {'/'.join(app_types)}")

    if item.get("rentalAssistance"):
        # Trim the verbose legal boilerplate to just the key fact
        ra = item["rentalAssistance"]
        if "voucher" in ra.lower() or "section 8" in ra.lower():
            notes_parts.append("accepts Section 8 / vouchers")

    if item.get("section8Acceptance"):
        # Explicit field added in newer Bloom versions; belt-and-suspenders
        sec8 = str(item["section8Acceptance"]).lower()
        if sec8 not in ("false", "none", ""):
            notes_parts.append(f"section8: {item['section8Acceptance']}")

    notes = "; ".join(notes_parts) if notes_parts else f"San José listing id:{listing_id}"

    # Confidence scoring: high if we have address + contact, medium otherwise.
    # These are authoritative records from the city's own portal, so we never
    # go below medium even if some fields are sparse.
    confidence: str = "high" if (address and (phone or email)) else "medium"

    return HousingRecord(
        authority="City of San José",
        property_name=name,
        address=address,
        phone=phone,
        email=email,
        property_manager=manager,
        community_type=community,
        bedrooms=bedrooms,
        supportive_services=services,
        notes=notes,
        document_url=detail_url,
        confidence=confidence,
    )


# =============================================================================
# SSR PRIMARY PATH
# =============================================================================

def _fetch_via_ssr() -> tuple[list[dict], list[dict]]:
    """
    Primary extraction path: polite HTTP GET + __NEXT_DATA__ JSON parsing.

    Returns (open_listings, closed_listings) as raw dicts.
    Returns ([], []) if __NEXT_DATA__ is absent or malformed.

    WHY this is the primary path (not Playwright):
      - /listings is SSR (getServerSideProps) — the server renders the full
        listing dataset into __NEXT_DATA__ before sending the HTML response.
      - polite_get() is ~10x faster than launching a Playwright browser.
      - Fewer moving parts = more reliable for daily unattended runs.

    IF THIS BREAKS: see the module docstring "PLAYWRIGHT FALLBACK" section and
    the _fetch_via_playwright() function below. The most likely causes are:
      1. The /listings route changed from SSR to CSR (client-side rendering).
         Sign: __NEXT_DATA__ tag is present but pageProps is empty or missing
         openListings/closedListings.
      2. The city migrated to a new Bloom Housing version with a different URL.
         Sign: HTTP redirect to a new domain or a 404.
      3. The Bloom platform added authentication in front of the listings page.
         Sign: HTTP 401 or redirect to a login page.
    """
    resp = polite_get(_LISTINGS_URL)
    if not resp:
        logger.warning("[SanJosé] SSR path: polite_get(%s) returned no response", _LISTINGS_URL)
        return [], []

    if resp.status_code != 200:
        logger.warning("[SanJosé] SSR path: HTTP %d for %s", resp.status_code, resp.url)
        return [], []

    soup = BeautifulSoup(resp.text, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        logger.warning(
            "[SanJosé] SSR path: __NEXT_DATA__ tag not found in %s response. "
            "The route may have switched to client-side rendering. "
            "Playwright fallback will activate.",
            _LISTINGS_URL,
        )
        return [], []

    try:
        data = json.loads(tag.string)
    except json.JSONDecodeError as exc:
        logger.warning("[SanJosé] SSR path: failed to parse __NEXT_DATA__ JSON: %s", exc)
        return [], []

    # IMPORTANT: the standard Next.js shape nests page data under
    # data["props"]["pageProps"], NOT data["pageProps"].
    # The top-level "pageProps" key does not exist; accessing it directly
    # returns an empty dict and silently yields zero listings.
    pp = data.get("props", {}).get("pageProps", {})

    open_l = pp.get("openListings", [])
    closed_l = pp.get("closedListings", [])

    if not isinstance(open_l, list) or not isinstance(closed_l, list):
        logger.warning(
            "[SanJosé] SSR path: pageProps found but openListings/closedListings "
            "are not lists. Schema may have changed. Keys present: %s",
            list(pp.keys()),
        )
        return [], []

    logger.info(
        "[SanJosé] SSR path: %d open + %d closed listings from __NEXT_DATA__",
        len(open_l),
        len(closed_l),
    )
    return open_l, closed_l


# =============================================================================
# PLAYWRIGHT FALLBACK
# =============================================================================

def _fetch_via_playwright() -> tuple[list[dict], list[dict]]:
    """
    Fallback extraction path: Playwright browser + JSON network interception.

    This activates only when the SSR path yields zero listings. It launches a
    headless Chromium browser, navigates to /listings, and intercepts all JSON
    network responses looking for the openListings/closedListings payload.

    On newer Bloom Housing versions the listings data may be fetched via XHR
    after page load rather than embedded in __NEXT_DATA__. This fallback
    handles that case by capturing whatever JSON responses the browser receives.

    WHEN THIS IS CALLED:
      - __NEXT_DATA__ was absent or empty from the SSR path.
      - This is NOT the normal daily code path — if it activates frequently,
        investigate whether the site's architecture has changed permanently.

    PERFORMANCE:
      - Playwright browser launch adds ~5–10s per run.
      - The network spy captures ALL JSON responses, not just listings — the
        _find_listing_arrays() heuristic filters out non-listing blobs.

    HOW TO KNOW IF THIS IS RUNNING:
      - Look for the log line "[SanJosé] Playwright fallback activated"
      - The daily run for San José will take longer than usual.
    """
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        logger.error(
            "[SanJosé] Playwright not installed. Cannot run fallback. "
            "Install it with: pip install playwright && playwright install chromium"
        )
        return [], []

    logger.warning("[SanJosé] Playwright fallback activated — SSR path yielded no data")

    captured_payloads: list[dict] = []

    def _on_response(response):
        """
        Network spy: intercept every JSON response the browser receives.
        We cast a wide net here — filtering happens in _find_listing_arrays().
        """
        try:
            ct = (response.headers.get("content-type") or "").lower()
            if "json" not in ct:
                return
            body = response.text()
            if len(body) > 3_000_000:
                # Safety valve: skip enormous blobs that can't be listing data
                return
            payload = response.json()
            captured_payloads.append({"url": response.url, "data": payload})
        except Exception:
            pass  # Non-JSON or connection error; not unusual during page load

    with sync_playwright() as pw:
        browser = pw.chromium.launch(headless=True)
        page = browser.new_page()
        page.on("response", _on_response)

        # /listings is the SSR route; if we're here it may have changed to CSR,
        # so we load it and wait for the browser to fetch the data via XHR.
        page.goto(_LISTINGS_URL, wait_until="networkidle", timeout=120_000)
        page.wait_for_timeout(4_000)  # extra wait for lazy-loaded XHR

        # Scroll to trigger any infinite-scroll or lazy-load mechanisms
        try:
            page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
            page.wait_for_timeout(2_500)
        except Exception:
            pass

        browser.close()

    logger.info(
        "[SanJosé] Playwright fallback: captured %d JSON responses",
        len(captured_payloads),
    )

    # Look for openListings/closedListings at any depth in captured payloads.
    # The Bloom Housing XHR response typically has the same shape as pageProps.
    for payload in captured_payloads:
        data = payload.get("data", {})
        # Direct match (XHR returns pageProps-equivalent object)
        if isinstance(data, dict):
            open_l = data.get("openListings") or data.get("props", {}).get("pageProps", {}).get("openListings", [])
            closed_l = data.get("closedListings") or data.get("props", {}).get("pageProps", {}).get("closedListings", [])
            if isinstance(open_l, list) and len(open_l) > 0:
                logger.info(
                    "[SanJosé] Playwright fallback: found listings in response from %s",
                    payload["url"],
                )
                return open_l, closed_l or []

    # Absolute last resort: heuristic scan of all captured JSON blobs
    logger.warning(
        "[SanJosé] Playwright fallback: no direct openListings/closedListings found. "
        "Trying heuristic array scan across %d captured payloads.",
        len(captured_payloads),
    )
    all_candidates: list[dict] = []
    for payload in captured_payloads:
        for arr in _find_listing_arrays(payload.get("data")):
            all_candidates.extend(arr)

    return all_candidates, []


# =============================================================================
# HEURISTIC FALLBACK (used by Playwright path)
# =============================================================================

def _looks_like_housing_item(item: dict) -> bool:
    """
    Quick filter to skip config blobs, feature flags, and other non-listing JSON
    that Playwright's network spy captures during page load.

    Bloom Housing listing objects always have "name" + "id" at minimum.
    Feature flag objects tend to have keys starting with "enable", "show", "limit".
    """
    if not isinstance(item, dict):
        return False
    keys = {str(k).lower() for k in item.keys()}

    # Reject obvious non-listing config blobs
    flag_prefixes = ("enable", "show", "limit", "export", "allow", "require")
    if sum(1 for k in keys if any(k.startswith(p) for p in flag_prefixes)) > 3:
        return False
    if "buildid" in keys or "runtime" in keys:
        return False

    # Bloom listing objects: "name" + "id" or "status" is sufficient evidence
    if "name" in keys and ("id" in keys or "status" in keys or "listingsbuildingaddress" in keys):
        return True

    strong = {"name", "title", "property", "address", "status", "application", "id", "slug"}
    medium = {"bedrooms", "units", "ami", "income", "manager", "contact", "rent"}
    score = len(keys & strong) + 0.5 * len(keys & medium)
    return score >= 1.5


def _find_listing_arrays(obj: Any, depth: int = 0, max_depth: int = 8) -> list[list]:
    """
    Recursively hunt for arrays of plausible housing listing objects in
    an arbitrary JSON structure. Used only by the Playwright heuristic fallback.

    Returns a list of arrays (not items) so the caller can choose how many
    arrays to combine vs. deduplicate.
    """
    if depth > max_depth or obj is None:
        return []
    found: list[list] = []
    if isinstance(obj, list):
        if len(obj) > 0 and all(isinstance(x, dict) for x in obj[:min(5, len(obj))]):
            if any(_looks_like_housing_item(x) for x in obj[:5]):
                found.append(obj)
        for child in obj:
            found.extend(_find_listing_arrays(child, depth + 1, max_depth))
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_find_listing_arrays(v, depth + 1, max_depth))
    return found


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================

def extract_san_jose_listings(max_results: int = 200) -> list[HousingRecord]:
    """
    Main entry point. Returns HousingRecord objects for all San José listings.

    Tries the fast SSR path first (polite_get + __NEXT_DATA__ parsing).
    Falls back to Playwright if the SSR path yields nothing.

    Both openListings and closedListings are returned:
      - openListings: actively accepting applications right now.
      - closedListings: currently not accepting applications, but represent
        real inventory the nonprofit wants to track (waitlist sizes, contact
        info, unit types) for outreach and referral.

    max_results applies to the combined open+closed total. Default 200 is
    intentionally high — the portal had 94 combined listings as of 2026-06-05
    and the nonprofit wants the full picture, not a paginated sample.
    """
    open_items, closed_items = _fetch_via_ssr()

    if not open_items and not closed_items:
        # SSR path returned nothing — activate Playwright fallback
        open_items, closed_items = _fetch_via_playwright()

    if not open_items and not closed_items:
        logger.error(
            "[SanJosé] Both SSR and Playwright paths returned zero listings. "
            "Manual investigation required. Check: "
            "(1) Is housing.sanjoseca.gov up? "
            "(2) Has the /listings route moved? "
            "(3) Is the portal behind a login wall now?"
        )
        return []

    records: list[HousingRecord] = []
    seen: set[str] = set()

    # Open listings first — they're higher priority for the nonprofit's daily use
    for item in open_items:
        uid = str(item.get("id") or item.get("name") or "")
        if uid in seen:
            continue
        seen.add(uid)
        rec = _san_jose_record_from_item(item)
        if rec:
            records.append(rec)
        if len(records) >= max_results:
            break

    for item in closed_items:
        if len(records) >= max_results:
            break
        uid = str(item.get("id") or item.get("name") or "")
        if uid in seen:
            continue
        seen.add(uid)
        rec = _san_jose_record_from_item(item)
        if rec:
            records.append(rec)

    logger.info(
        "[SanJosé] Produced %d HousingRecord objects (%d open + %d closed source items)",
        len(records),
        len(open_items),
        len(closed_items),
    )
    print(f"   [san_jose] {len(records)} records ({len(open_items)} open + {len(closed_items)} closed)")
    return records


# =============================================================================
# DIAGNOSTIC / OUTPUT HELPERS
# =============================================================================

def records_to_markdown(records: list[HousingRecord]) -> str:
    """Human-readable markdown table of extracted records."""
    if not records:
        return "_No records extracted._"

    lines = [
        "| Property | Address | Unit Types | Status / Notes | Contact / Apply |",
        "|----------|---------|------------|----------------|-----------------|",
    ]
    for r in records:
        name = (r.property_name or "(unnamed)")[:55]
        addr = (r.address or "")[:40]
        br = (r.bedrooms or "")[:22]
        notes = (r.notes or "")[:55].replace("|", "/")
        link = (r.document_url or "")
        if len(link) > 55:
            link = link[:52] + "..."
        lines.append(f"| {name} | {addr} | {br} | {notes} | {link} |")
    return "\n".join(lines)
