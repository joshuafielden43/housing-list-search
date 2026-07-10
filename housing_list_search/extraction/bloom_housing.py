"""
Bloom Housing Platform Adapter (First-Class)

This adapter handles ANY deployment of the open-source Bloom Housing platform
(github.com/bloom-housing/bloom). Bloom is used by multiple Bay Area cities and
regional portals. Write once, works everywhere Bloom is deployed.

Known instances as of 2026-06-05:
  housing.sanjoseca.gov       San José — SSR instance, /listings has __NEXT_DATA__
  housingbayarea.mtc.ca.gov   MTC Doorway (Bay Area regional) — CSR + REST API

New Bloom instances can be added to TARGETS.md with authority matching the city
and URL pointing to the instance's /listings page. No new adapter code required
unless the platform version introduces new field names.

=== THREE EXTRACTION PATHS (tried in order) ===

PATH 1 — SSR via __NEXT_DATA__ (preferred, fastest):
  Some Bloom instances use Next.js getServerSideProps on /listings. The full
  dataset is embedded in a <script id="__NEXT_DATA__"> tag before the browser
  runs any JavaScript. polite_get("/listings") returns the entire listing
  payload in ~2 seconds with no browser overhead.

  Data path: data["props"]["pageProps"]["openListings"] / ["closedListings"]

  IMPORTANT: data["pageProps"] does NOT exist at the top level in Next.js.
  The correct path is data["props"]["pageProps"]. Accessing data["pageProps"]
  silently returns an empty dict and yields zero listings — this was the bug
  in the original San José extractor.

  Known SSR instances: housing.sanjoseca.gov (confirmed 2026-06-05)

PATH 2 — REST API (for CSR instances like MTC Doorway):
  Some Bloom instances use client-side rendering; /listings __NEXT_DATA__ is
  empty. These instances expose a REST API at /api/adapter/listings/combined
  that the browser fetches via XHR after page load.

  How to call it (discovered by intercepting Playwright network traffic):
    POST https://{host}/api/adapter/listings/combined
    Headers:
      jurisdictionname: {jurisdiction name, e.g. "Bay Area"}
      appurl: https://{host}
      language: en
      content-type: application/json
    Body:
      {
        "view": "base",
        "limit": 100,
        "page": 1,
        "filter": [{"$comparison": "IN", "counties": ["{county}"]}],
        "orderBy": ["mostRecentlyPublished"],
        "orderDir": ["desc"]
      }

  The API returns {"items": [...], "meta": {...}} where items are listing objects.
  The "base" view omits some fields (e.g. units[], reservedCommunityTypes) compared
  to the SSR "full" view. Contact info and address are present.

  To filter to a specific city: filter items by
  item["listingsBuildingAddress"]["city"] == city_name after fetching.

  Known CSR/API instances: housingbayarea.mtc.ca.gov (confirmed 2026-06-05)

PATH 3 — Playwright fallback (last resort):
  If both SSR and API paths yield zero results, Playwright launches a headless
  Chromium browser and intercepts all JSON network responses. This handles:
  - Instances that change from SSR to CSR without notice
  - Instances with unusual authentication or routing
  It is deliberately slower (~10s) so that it only runs as a fallback.

  Signs the fallback has activated:
  - Log: "[Bloom] Playwright fallback activated for {url}"
  - The daily run for this target takes noticeably longer

=== ADDING A NEW BLOOM HOUSING INSTANCE ===

1. Add a row to TARGETS.md with the /listings URL and authority name.
2. Add the domain to BLOOM_DOMAINS in bloom_housing.py so the
   dispatcher routes it here.
3. If the instance is SSR: no code changes needed, Path 1 handles it.
4. If the instance is CSR (API): add it to _API_INSTANCES with the correct
   jurisdictionname and county. Path 2 handles it.
5. Run a test: python -c "from housing_list_search.extraction.bloom_housing
   import extract_bloom_housing_listings; print(len(
   extract_bloom_housing_listings('https://your-instance.gov/listings')))"

=== FIELD REFERENCE (Bloom Housing listing object) ===

Top-level fields used by this adapter:
  id                      UUID — stable identifier across instances
  name                    Property name (always present)
  status                  "active" | "closed" | "pending"
  urlSlug                 URL-safe slug for the detail page URL
  listingsBuildingAddress {street, city, county, state, zipCode, lat, lon}
  leasingAgentPhone       Contact phone
  leasingAgentEmail       Contact email
  leasingAgentName        Property manager / leasing contact name
  developer               Management company (fallback for leasingAgentName)
  units[]                 Per-unit objects: numBedrooms, amiPercentage, monthlyRent
  unitsSummarized.byUnitTypeAndRent[]  Aggregated rent/income ranges per type
  servicesOffered         Supportive services text
  reservedCommunityTypes  [{name: "Senior"}, ...] or null
  isWaitlistOpen / waitlistOpenSpots / waitlistCurrentSize
  applicationDueDate      ISO datetime
  applicationFee          string ("25")
  digitalApplication / paperApplication  bool
  reviewOrderType         "firstComeFirstServe" | "lottery"
  rentalAssistance        Voucher/Section 8 acceptance text
  marketingType           "marketing" | "comingSoon"

UNIT FIELDS:
  numBedrooms    int (0 = studio)
  amiPercentage  string ("60") — % of Area Median Income
  monthlyRent    string ("2250")
  unitTypes.name "studio"|"oneBdrm"|"twoBdrm"|"threeBdrm"|"fourBdrm"
"""

from __future__ import annotations

import json
import logging
import re
from dataclasses import dataclass, field
from typing import Any, Literal

try:
    from bs4 import BeautifulSoup
except ImportError:
    BeautifulSoup = None

from datetime import UTC

from housing_list_search.access import polite_get
from housing_list_search.extraction.pdf import HousingRecord

logger = logging.getLogger(__name__)

BloomPathName = Literal["ssr", "api", "playwright", "empty"]


@dataclass
class BloomRawInventory:
    """Raw listing items from one Bloom fetch path (#1064).

    Path adapters (SSR / API / Playwright) return this shape; the shared mapper
    turns items into HousingRecord. pagination_complete=False means partial feed
    (#1058 → SCRAPE_FAILED after mapping).
    """

    open_items: list[dict] = field(default_factory=list)
    closed_items: list[dict] = field(default_factory=list)
    pagination_complete: bool = True
    path: BloomPathName = "empty"

    @property
    def empty(self) -> bool:
        return not self.open_items and not self.closed_items

# Bloom instances that use the REST API rather than SSR.
# Key: hostname. Value: dict of API config for that instance.
#
# To add a new API instance: add its hostname here with the jurisdictionname
# (from the Bloom admin config) and the county filter name.
# The endpoint path (/api/adapter/listings/combined) is the same for all instances.
BLOOM_DOMAINS = frozenset(
    {
        "housing.sanjoseca.gov",
        "housingbayarea.mtc.ca.gov",
    }
)

_API_INSTANCES: dict[str, dict] = {
    "housingbayarea.mtc.ca.gov": {
        "jurisdictionname": "Bay Area",
        "endpoint": "https://housingbayarea.mtc.ca.gov/api/adapter/listings/combined",
    },
}


def is_bloom_url(url: str) -> bool:
    from urllib.parse import urlparse

    return urlparse(url).netloc.lower() in BLOOM_DOMAINS


def extract_bloom_for_target(url: str, authority: str = "") -> list[HousingRecord]:
    """Bloom extraction with MTC Doorway city_filter derived from authority."""
    u = (url or "").lower()
    city_filter = ""
    if "housingbayarea.mtc.ca.gov" in u and authority:
        city_filter = authority.replace("City of ", "").replace("Town of ", "")
        city_filter = re.sub(r"\s*\(.*\)\s*$", "", city_filter).strip()
    return extract_bloom_housing_listings(url, authority=authority, city_filter=city_filter)


def _listing_detail_url(listings_url: str, listing_id: str, slug: str) -> str:
    """Build the detail URL for a listing given the instance's base URL."""
    from urllib.parse import urlparse

    parsed = urlparse(listings_url)
    base = f"{parsed.scheme}://{parsed.netloc}/listing"
    if slug:
        return f"{base}/{listing_id}/{slug}"
    elif listing_id:
        return f"{base}/{listing_id}"
    return listings_url


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


def _bloom_record_from_item(item: dict, listings_url: str, authority: str) -> HousingRecord | None:
    """
    Map one Bloom Housing listing object → HousingRecord.

    Called for both openListings and closedListings from any Bloom instance.
    Closed listings are included — they represent real inventory the nonprofit
    wants to track (waitlist sizes, contact info, unit types) for outreach.

    listings_url: the full URL used to fetch this instance (e.g.
        "https://housing.sanjoseca.gov/listings"). Used to build detail URLs.
    authority: the city/authority label for this record (e.g. "City of San José").
    """
    if not isinstance(item, dict):
        return None

    name = (item.get("name") or "").strip()
    if not name:
        # Every real listing has a name; unnamed objects are usually metadata
        return None

    listing_id = item.get("id") or ""
    slug = item.get("urlSlug") or ""
    detail_url = _listing_detail_url(listings_url, listing_id, slug)

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
        listing_status = "coming_soon"
    elif status == "active":
        notes_parts.append("accepting applications")
        listing_status = "open"
    elif status == "closed":
        notes_parts.append("closed — not currently accepting applications")
        listing_status = "closed"
    else:
        listing_status = status or ""
        if status:
            notes_parts.append(f"status: {status}")

    if review == "lottery":
        lottery_status = (item.get("lotteryStatus") or "").strip()
        notes_parts.append(f"lottery system{': ' + lottery_status if lottery_status else ''}")
    elif review == "firstComeFirstServe":
        notes_parts.append("first-come first-served")

    if item.get("isWaitlistOpen"):
        spots = item.get("waitlistOpenSpots")
        notes_parts.append(f"waitlist open ({spots} spots)" if spots else "waitlist open")
        if listing_status != "open":
            listing_status = "waitlist"
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

    notes = "; ".join(notes_parts) if notes_parts else f"Bloom listing id:{listing_id}"

    # Confidence scoring: high if we have address + contact, medium otherwise.
    # These are authoritative records from the portal itself, so we never
    # go below medium even if some fields are sparse.
    confidence: str = "high" if (address and (phone or email)) else "medium"

    from datetime import datetime

    now = datetime.now(UTC).isoformat()

    return HousingRecord(
        authority=authority,
        property_name=name,
        address=address,
        phone=phone,
        email=email,
        property_manager=manager,
        community_type=community,
        bedrooms=bedrooms,
        supportive_services=services,
        notes=notes,
        listing_status=listing_status,
        document_url=detail_url,
        confidence=confidence,
        last_seen=now,
        first_seen=now,
        source=f"bloom:{listing_id}" if listing_id else "bloom",
        source_url=detail_url,
    )


# =============================================================================
# SSR PRIMARY PATH
# =============================================================================


def _fetch_via_ssr(listings_url: str) -> tuple[list[dict], list[dict]]:
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
    resp = polite_get(listings_url)
    if not resp:
        raise RuntimeError(f"[Bloom] SSR path: polite_get returned no response for {listings_url}")

    if resp.status_code != 200:
        raise RuntimeError(f"[Bloom] SSR path: HTTP {resp.status_code} for {resp.url}")

    soup = BeautifulSoup(resp.text, "html.parser")
    tag = soup.find("script", id="__NEXT_DATA__")
    if not tag or not tag.string:
        logger.warning(
            "[Bloom] SSR path: __NEXT_DATA__ tag not found in %s response. "
            "The route may have switched to client-side rendering. "
            "API or Playwright fallback will activate.",
            listings_url,
        )
        return [], []

    try:
        data = json.loads(tag.string)
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"[Bloom] SSR path: failed to parse __NEXT_DATA__ JSON: {exc}") from exc

    # IMPORTANT: the standard Next.js shape nests page data under
    # data["props"]["pageProps"], NOT data["pageProps"].
    # The top-level "pageProps" key does not exist; accessing it directly
    # returns an empty dict and silently yields zero listings.
    # Tolerant to minor variations in structure for robustness against site changes.
    pp = data.get("props", {}).get("pageProps", {}) or data.get("pageProps", {}) or {}

    open_l = pp.get("openListings") or pp.get("listings") or []
    closed_l = pp.get("closedListings") or []

    if not isinstance(open_l, list) or not isinstance(closed_l, list):
        logger.warning(
            "[Bloom] SSR path: pageProps found but openListings/closedListings "
            "are not lists. Schema may have changed. Keys present: %s",
            list(pp.keys()),
        )
        return [], []

    logger.info(
        "[Bloom] SSR path: %d open + %d closed listings from %s __NEXT_DATA__",
        len(open_l),
        len(closed_l),
        listings_url,
    )
    return open_l, closed_l


# =============================================================================
# REST API PATH (CSR instances like MTC Doorway)
# =============================================================================


def _fetch_via_api(
    listings_url: str, city_filter: str = ""
) -> tuple[list[dict], list[dict], bool]:
    """
    REST API extraction path for Bloom instances that use client-side rendering.

    Returns (open_items, closed_items, pagination_complete). When
    pagination_complete is False the caller must treat the authority as
    failed (had_error / SCRAPE_FAILED) even if some items were returned (#1058).

    Bloom CSR instances (e.g. housingbayarea.mtc.ca.gov) load the page as a
    shell and fetch listing data via XHR POST to /api/adapter/listings/combined.
    This function replicates that request directly without a browser.

    API details discovered by intercepting Playwright network traffic on
    housingbayarea.mtc.ca.gov (confirmed 2026-06-05):
      POST /api/adapter/listings/combined
      Required headers:
        jurisdictionname: {name from _API_INSTANCES config}
        appurl: https://{host}
        language: en
        accept: application/json
        content-type: application/json
      Body: {"view":"base","limit":100,"page":1,
             "filter":[{"$comparison":"IN","counties":["{county}"]}],
             "orderBy":["mostRecentlyPublished"],"orderDir":["desc"]}

    Returns all items as "open_listings" (the API mixes open/closed via status
    field). closed_listings is always returned empty — the caller inspects
    item["status"] for filtering.

    city_filter: if non-empty, only return items whose
        listingsBuildingAddress.city matches (case-insensitive).
    """
    from urllib.parse import urlparse

    from housing_list_search.access import URLPolicyError, polite_post, validate_http_url

    parsed = urlparse(listings_url)
    host = parsed.netloc  # e.g. "housingbayarea.mtc.ca.gov"

    cfg = _API_INSTANCES.get(host)
    if not cfg:
        logger.debug("[Bloom] API path: %s is not in _API_INSTANCES — skipping", host)
        return [], [], True  # not applicable; not an incomplete failure

    endpoint = cfg["endpoint"]
    try:
        validate_http_url(endpoint)
    except URLPolicyError as exc:
        logger.warning("[Bloom] API path: endpoint blocked by URL policy: %s", exc)
        return [], [], False

    jurisdiction = cfg["jurisdictionname"]

    headers = {
        "jurisdictionname": jurisdiction,
        "appurl": f"https://{host}",
        "language": "en",
        "accept": "application/json",
        "content-type": "application/json",
        # Browser-like User-Agent avoids some bot-detection rejections
        "user-agent": (
            "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) "
            "AppleWebKit/537.36 (KHTML, like Gecko) "
            "Chrome/124.0.0.0 Safari/537.36"
        ),
    }

    # Page through the full dataset. The API is paginated; a single page-1 request
    # ordered by mostRecentlyPublished can miss older listings that still exist and
    # are perfectly valid (e.g. Santa Clara city listings in the Bay Area-wide feed).
    # We fetch until the API signals no more pages via meta.totalItems or an empty page.
    page_size = 100
    all_items: list[dict] = []
    page = 1
    max_pages = 20  # safety cap (~2,000 listings max; the entire Bay Area has far fewer)
    pagination_complete = True
    last_page_count = 0

    while page <= max_pages:
        body = {
            "view": "base",
            "limit": page_size,
            "page": page,
            "orderBy": ["mostRecentlyPublished"],
            "orderDir": ["desc"],
            "filter": [],
        }

        resp = polite_post(endpoint, json=body, headers=headers)
        if resp is None:
            logger.warning(
                "[Bloom] API path: request to %s page %d failed or was blocked", endpoint, page
            )
            pagination_complete = False
            break

        if not (200 <= resp.status_code < 300):
            logger.warning(
                "[Bloom] API path: HTTP %d from %s page %d", resp.status_code, endpoint, page
            )
            pagination_complete = False
            break
        if resp.status_code != 200:
            logger.info(
                "[Bloom] API path: HTTP %d from %s page %d (accepted 2xx)",
                resp.status_code,
                endpoint,
                page,
            )

        try:
            payload = resp.json()
        except Exception as exc:
            logger.warning(
                "[Bloom] API path: failed to parse JSON from %s page %d: %s", endpoint, page, exc
            )
            pagination_complete = False
            break

        page_items = payload.get("items") or []
        if not isinstance(page_items, list):
            logger.warning(
                "[Bloom] API path: unexpected payload shape from %s page %d", endpoint, page
            )
            pagination_complete = False
            break

        last_page_count = len(page_items)
        all_items.extend(page_items)

        # Stop if we got fewer items than the page size (last page) or the API
        # tells us via meta.totalItems that we've collected everything
        meta = payload.get("meta") or {}
        total = meta.get("totalItems")
        if total is not None and len(all_items) >= int(total):
            break
        if len(page_items) < page_size:
            break

        page += 1
    else:
        if last_page_count >= page_size:
            logger.error(
                "[Bloom] API path: pagination hit max_pages=%d before natural end — aborting",
                max_pages,
            )
            pagination_complete = False

    if not pagination_complete:
        if all_items:
            logger.warning(
                "[Bloom] API path: pagination incomplete — %d partial item(s) from %s "
                "(will mark scrape failed; partial inventory is not a successful full feed)",
                len(all_items),
                endpoint,
            )
        else:
            logger.error(
                "[Bloom] API path: pagination aborted with zero items from %s",
                endpoint,
            )
            return [], [], False

    items = all_items

    # Apply optional city filter (e.g. isolate "Santa Clara" city from the
    # MTC Bay Area-wide feed which covers the whole county).
    # Applied after full pagination so no item is missed due to sort order.
    if city_filter:
        cf = city_filter.lower()
        items = [
            it
            for it in items
            if (it.get("listingsBuildingAddress") or {}).get("city", "").lower() == cf
        ]

    logger.info(
        "[Bloom] API path: %d items from %s across %d page(s) (city_filter=%r complete=%s)",
        len(items),
        endpoint,
        page,
        city_filter,
        pagination_complete,
    )
    # Return all as open_listings; status field within each item distinguishes
    # open vs closed and is preserved in _bloom_record_from_item.
    # pagination_complete is the third element (#1058).
    return items, [], pagination_complete


# =============================================================================
# PLAYWRIGHT FALLBACK
# =============================================================================


def _fetch_via_playwright(listings_url: str) -> tuple[list[dict], list[dict]]:
    """
    Fallback extraction path: Playwright browser + JSON network interception.

    This activates only when both SSR and API paths yield zero listings. It
    launches a headless Chromium browser, navigates to /listings, and intercepts
    all JSON network responses looking for the openListings/closedListings payload.

    On newer Bloom Housing versions the listings data may be fetched via XHR
    after page load rather than embedded in __NEXT_DATA__. This fallback
    handles that case by capturing whatever JSON responses the browser receives.

    WHEN THIS IS CALLED:
      - __NEXT_DATA__ was absent or empty from the SSR path.
      - The instance is not in _API_INSTANCES (or the API also returned nothing).
      - This is NOT the normal daily code path — if it activates frequently,
        investigate whether the site's architecture has changed permanently.

    PERFORMANCE:
      - Playwright browser launch adds ~5–10s per run.
      - The network spy captures ALL JSON responses, not just listings — the
        _find_listing_arrays() heuristic filters out non-listing blobs.

    HOW TO KNOW IF THIS IS RUNNING:
      - Look for the log line "[Bloom] Playwright fallback activated"
      - The daily run for this target will take longer than usual.
    """
    try:
        from housing_list_search.access import browser_page, safe_goto
    except ImportError:
        logger.error(
            "[Bloom] Playwright not installed. Cannot run fallback. "
            "Install it with: pip install playwright && playwright install chromium"
        )
        return [], []

    # Last-resort path only (#987): SSR/API already returned empty.
    logger.warning(
        "[Bloom] Playwright fallback activated for %s — SSR/API paths yielded no data", listings_url
    )

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

    try:
        with browser_page() as page:
            page.on("response", _on_response)
            try:
                safe_goto(page, listings_url, wait_until="domcontentloaded", timeout=45_000)
                page.wait_for_timeout(3_000)  # allow XHR without networkidle (SPAs never settle)
                try:
                    page.evaluate("window.scrollTo(0, document.body.scrollHeight)")
                    page.wait_for_timeout(1_500)
                except Exception:
                    pass
            except Exception as exc:
                logger.warning(
                    "[Bloom] Playwright fallback navigation failed for %s: %s", listings_url, exc
                )
    except Exception as exc:
        logger.warning("[Bloom] Playwright fallback failed for %s: %s", listings_url, exc)
        return [], []

    logger.info(
        "[Bloom] Playwright fallback: captured %d JSON responses for %s",
        len(captured_payloads),
        listings_url,
    )

    # Look for openListings/closedListings at any depth in captured payloads.
    # The Bloom Housing XHR response typically has the same shape as pageProps.
    for payload in captured_payloads:
        data = payload.get("data", {})
        # Direct match (XHR returns pageProps-equivalent object)
        if isinstance(data, dict):
            open_l = data.get("openListings") or data.get("props", {}).get("pageProps", {}).get(
                "openListings", []
            )
            closed_l = data.get("closedListings") or data.get("props", {}).get("pageProps", {}).get(
                "closedListings", []
            )
            if isinstance(open_l, list) and len(open_l) > 0:
                logger.info(
                    "[Bloom] Playwright fallback: found listings in response from %s",
                    payload["url"],
                )
                return open_l, closed_l or []

    # Absolute last resort: heuristic scan of all captured JSON blobs
    logger.warning(
        "[Bloom] Playwright fallback: no direct openListings/closedListings found. "
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
        if len(obj) > 0 and all(isinstance(x, dict) for x in obj[: min(5, len(obj))]):
            if any(_looks_like_housing_item(x) for x in obj[:5]):
                found.append(obj)
        for child in obj:
            found.extend(_find_listing_arrays(child, depth + 1, max_depth))
    elif isinstance(obj, dict):
        for v in obj.values():
            found.extend(_find_listing_arrays(v, depth + 1, max_depth))
    return found


# =============================================================================
# PATH ORCHESTRATION + SHARED MAPPER (#1064)
# =============================================================================


def _filter_items_by_city(items: list[dict], city_filter: str) -> list[dict]:
    """Keep items whose listingsBuildingAddress.city matches city_filter."""
    if not city_filter:
        return items
    cf = city_filter.lower()
    return [
        it
        for it in items
        if (it.get("listingsBuildingAddress") or {}).get("city", "").lower() == cf
    ]


def map_bloom_inventory_to_records(
    inventory: BloomRawInventory,
    *,
    listings_url: str,
    authority: str,
    max_results: int = 200,
) -> list[HousingRecord]:
    """Shared mapper: raw open/closed items → HousingRecord (all fetch paths)."""
    records: list[HousingRecord] = []
    seen: set[str] = set()

    # Open listings first — higher priority for the nonprofit's daily use
    for item in inventory.open_items:
        uid = str(item.get("id") or item.get("name") or "")
        if uid in seen:
            continue
        seen.add(uid)
        rec = _bloom_record_from_item(item, listings_url, authority)
        if rec:
            records.append(rec)
        if len(records) >= max_results:
            break

    for item in inventory.closed_items:
        if len(records) >= max_results:
            break
        uid = str(item.get("id") or item.get("name") or "")
        if uid in seen:
            continue
        seen.add(uid)
        rec = _bloom_record_from_item(item, listings_url, authority)
        if rec:
            records.append(rec)

    return records


def resolve_bloom_inventory(
    url: str,
    *,
    city_filter: str = "",
) -> BloomRawInventory:
    """
    Try Bloom fetch-path adapters in order: SSR → REST API → Playwright.

    Each path is independently testable via ``_fetch_via_*``. This function only
    sequences them and normalizes to BloomRawInventory.
    """
    from urllib.parse import urlparse

    # Path 1 — SSR adapter
    open_items, closed_items = _fetch_via_ssr(url)
    if open_items or closed_items:
        return BloomRawInventory(
            open_items=_filter_items_by_city(open_items, city_filter),
            closed_items=_filter_items_by_city(closed_items, city_filter),
            pagination_complete=True,
            path="ssr",
        )

    # Path 2 — REST API adapter (CSR instances)
    open_items, closed_items, pagination_complete = _fetch_via_api(
        url, city_filter=city_filter
    )
    if open_items or closed_items or not pagination_complete:
        return BloomRawInventory(
            open_items=open_items,
            closed_items=closed_items,
            pagination_complete=pagination_complete,
            path="api",
        )

    # Path 3 — Playwright adapter (last resort; skip known API hosts)
    api_host = urlparse(url).netloc
    if api_host not in _API_INSTANCES:
        open_items, closed_items = _fetch_via_playwright(url)
        if open_items or closed_items:
            return BloomRawInventory(
                open_items=_filter_items_by_city(open_items, city_filter),
                closed_items=_filter_items_by_city(closed_items, city_filter),
                pagination_complete=True,
                path="playwright",
            )

    return BloomRawInventory(path="empty")


# =============================================================================
# PUBLIC ENTRY POINT
# =============================================================================


def extract_bloom_housing_listings(
    url: str,
    authority: str = "",
    city_filter: str = "",
    max_results: int = 200,
) -> list[HousingRecord]:
    """
    Main entry point. Returns HousingRecord objects for any Bloom Housing instance.

    Path adapters (SSR / API / Playwright) feed BloomRawInventory; one shared
    mapper produces HousingRecord objects (#1064).

    url: the /listings URL of the Bloom instance to extract from.
    authority: label for records (e.g. "City of San José"). Inferred from URL
        host if not provided.
    city_filter: if provided, only return listings whose city matches this
        string (case-insensitive). Useful for CSR instances that serve a whole
        county (e.g. MTC Doorway) when you only want one city.
    max_results: cap on combined open+closed records returned. Default 200 is
        intentionally high — the nonprofit wants the full inventory picture.
    """
    from urllib.parse import urlparse

    from housing_list_search.access import SourceFetchError

    if not authority:
        authority = urlparse(url).netloc

    inventory = resolve_bloom_inventory(url, city_filter=city_filter)

    if inventory.empty:
        if not inventory.pagination_complete:
            raise SourceFetchError(
                f"[Bloom] API pagination failed with zero listings for {url}"
            )
        logger.error(
            "[Bloom] All three extraction paths returned zero listings for %s. "
            "Manual investigation required. Check: "
            "(1) Is the portal up? "
            "(2) Has the /listings route moved? "
            "(3) Is authentication now required?",
            url,
        )
        return []

    records = map_bloom_inventory_to_records(
        inventory,
        listings_url=url,
        authority=authority,
        max_results=max_results,
    )

    if not inventory.pagination_complete:
        # #1058: partial regional feed is not a successful complete inventory
        raise SourceFetchError(
            f"[Bloom] incomplete API pagination for {url} "
            f"({len(records)} records converted; mark SCRAPE_FAILED)",
            partial=list(records),
        )

    logger.info(
        "[Bloom] Produced %d HousingRecord objects from %s via %s "
        "(%d open + %d closed source items)",
        len(records),
        url,
        inventory.path,
        len(inventory.open_items),
        len(inventory.closed_items),
    )
    print(
        f"   [bloom_housing] {len(records)} records "
        f"({len(inventory.open_items)} open + {len(inventory.closed_items)} closed) "
        f"via {inventory.path} from {url}"
    )
    return records
