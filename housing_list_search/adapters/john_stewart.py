"""
John Stewart Company Adapter (Consolidated)

This is the single, canonical adapter for all properties managed or presented
through the John Stewart Company platform (primarily jscosccha.com).

It deliberately handles two different access patterns that point to the same
underlying data:

1. Direct access to the John Stewart vendor site (jscosccha.com/property/...)
2. Custom municipal front-ends, such as the Santa Clara County Housing
   Authority (SCCHA) "All Section 8 Properties" directory page, which is a
   homegrown WordPress index that heavily links to and curates John Stewart
   managed properties.

ARCHITECTURAL PRINCIPLE
-----------------------
Name adapters after the *tool or vendor platform*, not the housing authority
or city. When a municipality builds a custom front-end (common with WordPress
+ custom post types + Google Maps), detect the entry point inside the adapter
and parse accordingly. This prevents duplicate maintenance when the same
backend vendor is used across multiple counties.

This file serves as the reference implementation and template for future
one-off municipal adapters.

=============================================================================
SCOPE & GUARDRAILS
=============================================================================

This section defines the intended scope and principles for maintaining and
extending the adapter over time.

In Scope
- Extraction of property data from the John Stewart platform, whether
  accessed directly or through municipal front-ends that surface
  John Stewart-managed properties.
- Details that are publicly published on the platform or front-end pages
  (property name, address, phone, email, status, unit information,
  income requirements, and links to application documents).

Out of Scope
- Hunting for unlisted or internal contact information.
- Contacting individual staff members.

Known Low-Value Patterns
- Broad keyword scanning of vendor pages when more structured content
  exists on the same site. Future improvements should prefer targeted
  selectors over generic text searches.

Extension Guidance
- When a new city presents a custom front-end that primarily surfaces
  John Stewart properties, extend this adapter rather than creating a
  separate one.
- When a new tool or significantly different data pattern is encountered,
  create a new adapter and document the pattern so the overall capability
  improves over time.

The adapter is designed to be extended incrementally as more sites using
the same underlying platform are discovered.
=============================================================================

PATTERN FOR NEW ONE-OFF ADAPTERS
--------------------------------
When a new city presents a custom page that is *not* a standard vendor portal:

1. Create (or extend) one adapter file named after the real backend tool/vendor
   (e.g. john_stewart.py, not "sccha.py").

2. Implement a public `scrape_<tool>(url)` function that acts as a dispatcher.

3. Inside the dispatcher, inspect the URL (or page content) to decide which
   parser to use:
   - Custom city front-end parser (usually higher quality, structured cards)
   - Direct vendor backend parser

4. Keep *all* logic for that tool in this one file. Future cities using the
   same vendor should be able to reuse or lightly extend the same adapter.

5. Prefer parsing what is locally available on the custom page first.
   Only fall back to following external links when necessary.

6. Document the specific front-end vs backend relationship in the module
   docstring (see above).

This approach minimizes the number of adapters we maintain and makes it
obvious where to apply fixes when a vendor updates their platform.

Current status (as of May 2026):
- SCCHA directory page: 31 structured records via custom .property-box parser
- Direct jscosccha.com pages: heuristic extraction (can be improved per site)

Logging: Uses print() for operational visibility during runs (consistent with
other adapters in this project). Consider switching to structlog later if
centralized logging is adopted.
"""

from __future__ import annotations

import re
from datetime import datetime as _dt
from typing import Any

from bs4 import BeautifulSoup

from housing_list_search.scraper import polite_get

# Fallback canonical authority label for John Stewart sources when no
# TargetContext authority is supplied. The listing seam (canonical_authority
# in listing.py) unifies variants (including descriptive TARGETS names like
# "John Stewart Company (jsco.net portfolio)") to this stable key to avoid
# STALE churn across SCCHA, jscosccha.com, and jsco.net entries.
JOHN_STEWART_AUTHORITY = "John Stewart Company"


def _normalize(text: str) -> str:
    """Collapse whitespace and normalize a text blob."""
    return " ".join(text.split())


# =============================================================================
# CUSTOM FRONT-END PARSER: SCCHA Properties Directory
# =============================================================================
# The SCCHA maintains its own WordPress-based directory page that lists many
# properties managed by John Stewart. This parser targets the structured
# .property-box cards that the custom page renders. It is significantly more
# reliable than the generic keyword scraper used on the direct vendor site.
#
# This is the preferred path when the input URL is the SCCHA custom front-end.
# =============================================================================


def _scrape_sccha_directory(url: str, *, authority: str = "") -> list[dict[str, Any]]:
    """
    Parse SCCHA's custom WordPress properties grid.

    The page uses div.property-box elements containing name + address + tags
    (senior, Section 8, unit count, etc.) plus "Learn More" links that often
    point back to the real John Stewart property pages.
    """
    print(f"🧩 Running John Stewart adapter (SCCHA custom directory mode) on {url}")
    auth = authority or JOHN_STEWART_AUTHORITY

    from housing_list_search.scraper import require_response

    resp = require_response(polite_get(url), url, context="john_stewart/sccha_directory")

    soup = BeautifulSoup(resp.text, "html.parser")
    listings: list[dict[str, Any]] = []
    seen: set = set()

    boxes = soup.select("div.property-box, div.box.property-box")
    if not boxes:
        boxes = soup.find_all(
            "div", class_=lambda c: c and "property" in " ".join(c).lower() if c else False
        )

    for box in boxes:
        text = _normalize(box.get_text(" ", strip=True))

        # Address is the most reliable anchor
        addr_match = re.search(
            r"(\d{1,5}\s+[A-Za-z0-9\s\.\,\-]+(?:Ave|St|Street|Rd|Road|Dr|Drive|Blvd|Way|Ln|Lane|Ct|Court|Pl|Place|Circle)[^,]*,\s*(?:San Jose|San José|Santa Clara|Campbell|Cupertino|Sunnyvale|Milpitas|Los Gatos|Morgan Hill|Gilroy|Mountain View|Palo Alto|Los Altos)[^0-9]{0,10}\d{5})",
            text,
            re.I,
        )
        address = addr_match.group(1).strip() if addr_match else ""

        # Name = text before the address
        name = ""
        if address:
            before = text.split(address)[0].strip()
            words = [w for w in before.split() if len(w) > 1][:5]
            if words:
                name = " ".join(words)

        units_match = re.search(r"(\d+)\s*(?:Unit|Units)", text, re.I)
        units = units_match.group(1) if units_match else ""

        tags = []
        for kw in [
            "senior",
            "family",
            "tax credit",
            "section 8",
            "veteran",
            "disabled",
            "workforce",
        ]:
            if re.search(r"\b" + kw + r"\b", text, re.I):
                tags.append(kw.title())

        learn_more = box.find("a", string=re.compile("learn more|view|details|flyer", re.I))
        detail_url = ""
        if learn_more and learn_more.get("href"):
            href = learn_more["href"]
            detail_url = (
                href if href.startswith("http") else "https://www.scchousingauthority.org" + href
            )

        box_classes = " ".join(box.get("class", []))
        status = (
            "Open"
            if "open" in box_classes.lower() or "accepting" in text.lower()
            else "Check with owner"
        )

        if not address and not name:
            continue

        key = (name[:50].lower(), address[:50].lower())
        if key in seen:
            continue
        seen.add(key)

        now_iso = _dt.now().isoformat()
        listing = {
            "authority": auth,
            "property_name": name or address.split(",")[0].strip(),
            "address": address,
            "url": detail_url or url,
            "status": status,
            "deadline": "",
            "income_limits": "Section 8 / Tax Credit (varies by property)",
            "unit_types": f"{units} units" if units else "Varies",
            "eligibility_flags": ["section_8"]
            + (["senior"] if "senior" in " ".join(tags).lower() else []),
            "notes": " | ".join(tags)
            + (f" | flyer: {detail_url}" if detail_url and detail_url.endswith(".pdf") else ""),
            "confidence": 0.80,
            # Freshness metadata (0.8.2+)
            "last_seen": now_iso,
            "first_seen": now_iso,
            "source": "john_stewart:sccha_directory",
            "source_url": detail_url or url,
            "expires_at": "",
        }
        listings.append(listing)

    print(f"   → Extracted {len(listings)} properties from SCCHA directory (John Stewart backend)")
    return listings


# =============================================================================
# DIRECT VENDOR PARSER: jscosccha.com (John Stewart Platform)
# =============================================================================
# This is the baseline parser for pages that live directly on the John Stewart
# Company site. It uses a conservative keyword heuristic because the site
# structure varies and is not as cleanly card-based as the SCCHA custom page.
#
# This parser can (and should) be iteratively improved as we encounter more
# real John Stewart property pages. It is intentionally kept separate from
# the custom front-end parser so each can evolve independently.
# =============================================================================


def _scrape_direct_john_stewart(url: str, *, authority: str = "") -> list[dict[str, Any]]:
    """
    Robust parser for individual property pages on the John Stewart platform
    (jscosccha.com/property/...).

    These pages have a fairly consistent structure with a main content block
    containing description, address, phone, email, unit mix, rents, eligibility,
    amenities, and links to PDFs (Tenant Selection Criteria, Application, etc.).

    This parser extracts the key actionable fields so the record is useful
    even when John Stewart data is the background source.
    """
    print(f"🧩 Running John Stewart adapter (direct vendor site mode) on {url}")
    auth = authority or JOHN_STEWART_AUTHORITY

    from housing_list_search.scraper import require_response

    resp = require_response(polite_get(url), url, context="john_stewart/vendor_site")

    soup = BeautifulSoup(resp.text, "html.parser")
    listings: list[dict[str, Any]] = []

    # Prefer the main content area
    main = (
        soup.find("main")
        or soup.find("article")
        or soup.find(
            class_=lambda c: c
            and any(x in str(c).lower() for x in ["content", "entry", "property"])
        )
    )
    text = main.get_text(" ", strip=True) if main else soup.get_text(" ", strip=True)

    # Property name from title or first strong heading
    title = soup.title.string if soup.title else ""
    name = title.split(" - ")[0].strip() if " - " in title else title.strip()

    # Address
    addr_match = re.search(r"Address:\s*([^<]+?)(?:Phone|Email|previous|\n|$)", text, re.I)
    if not addr_match:
        addr_match = re.search(
            r"(\d{1,5}\s+[A-Za-z][A-Za-z0-9\s\.,\-]+(?:St|Street|Rd|Road|Dr|Drive|Way|Ln|Blvd|Ave|Court|Place)[^,]*,\s*[^,]+,\s*CA\s*\d{5})",
            text,
            re.I,
        )
    address = addr_match.group(1).strip() if addr_match else ""

    # Phone & Email
    phone = ""
    pm = re.search(r"Phone:\s*([\d\s\-\(\)]+)", text, re.I)
    if pm:
        phone = pm.group(1).strip()

    email = ""
    em = re.search(r"Email:\s*([^\s<>\"]+@[^\s<>\"]+)", text, re.I)
    if not em:
        em = re.search(r"([a-z0-9\.\-_+]+@jsco\.net)", text, re.I)
    if em:
        email = em.group(1).strip()

    # Status
    status = "Unknown"
    if re.search(r"Waitlist (is )?(currently )?open", text, re.I):
        status = "Waitlist Open"
    elif re.search(r"Waitlist (is )?closed", text, re.I):
        status = "Waitlist Closed"
    elif re.search(r"Application Status:\s*([^<\n]+)", text, re.I):
        status = re.search(r"Application Status:\s*([^<\n]+)", text, re.I).group(1).strip()

    # Housing type & income
    htype = ""
    tm = re.search(r"Housing Type:\s*([^<\n]+)", text, re.I)
    if tm:
        htype = tm.group(1).strip()

    income = ""
    im = re.search(r"Income Requirements:\s*([^<\n]+)", text, re.I)
    if im:
        income = im.group(1).strip()[:200]

    # Unit mix
    units = ""
    um = re.search(r"Unit Mix:\s*([^<\n]+)", text, re.I)
    if um:
        units = um.group(1).strip()
    else:
        um2 = re.search(r"(\d+)[ -]*(?:unit|bedroom|br)", text, re.I)
        if um2:
            units = f"{um2.group(1)} units"

    # Collect PDF links
    pdf_links = []
    for a in soup.find_all("a", href=True):
        href = a["href"]
        if href.lower().endswith(".pdf"):
            if not href.startswith("http"):
                href = (
                    "https://jscosccha.com" + href
                    if href.startswith("/")
                    else url.rsplit("/", 1)[0] + "/" + href
                )
            label = a.get_text(strip=True) or "PDF"
            pdf_links.append(f"{label}: {href}")

    notes_parts = []
    if htype:
        notes_parts.append(htype)
    if income:
        notes_parts.append(f"Income: {income[:120]}")
    if pdf_links:
        notes_parts.append(" | ".join(pdf_links[:3]))

    notes = " | ".join(notes_parts) if notes_parts else text[:300]

    if name or address:
        rec = {
            "authority": auth,
            "property_name": name or address.split(",")[0].strip(),
            "address": address,
            "phone": phone,
            "email": email,
            "url": url,
            "status": status,
            "income_limits": income,
            "unit_types": units,
            "eligibility_flags": ["section_8", "tax_credit"] + ([htype.lower()] if htype else []),
            "notes": notes,
            "confidence": "high" if (address and (phone or email)) else "medium",
            # Freshness metadata (0.8.2+)
            "last_seen": _dt.now().isoformat(),
            "first_seen": _dt.now().isoformat(),
            "source": "john_stewart:direct",
            "source_url": url,
            "expires_at": "",
        }
        listings.append(rec)

    print(f"   → Extracted {len(listings)} record(s) from direct John Stewart property page")
    return listings


# =============================================================================
# CORPORATE PORTFOLIO PARSER: jsco.net WordPress REST API
# =============================================================================
# John Stewart's corporate site (jsco.net) exposes its full property portfolio
# through the standard WordPress REST API with a `city` taxonomy. One request
# returns every Santa Clara County property (67 as of June 2026) — far broader
# coverage than the jscosccha.com waitlist site, which only lists SCCHA
# Section 8 properties. robots.txt allows general agents (Allow: /).
# theunitedeffort.org watches these same 67 property pages individually.
# =============================================================================

_JSCO_API_BASE = "https://jsco.net/wp-json/wp/v2"

# WordPress `city` taxonomy term IDs for Santa Clara County (verified 2026-06-10
# via /wp-json/wp/v2/city). If jsco.net adds a new county city, add its ID here.
_JSCO_SCC_CITIES = {
    23: "San Jose",
    52: "Campbell",
    56: "Santa Clara",
    108: "Gilroy",
    110: "Milpitas",
    220: "Palo Alto",
    221: "Mountain View",
    292: "Morgan Hill",
}


def _scrape_jsco_portfolio(url: str, *, authority: str = "") -> list[dict[str, Any]]:
    """Fetch all Santa Clara County properties from the jsco.net REST API.

    Single paginated query filtered by city taxonomy; returns one record per
    property with name, city, detail link, and the post's last-modified date
    (a genuine freshness signal maintained by the vendor).
    """
    import html as _html

    print("🧩 Running John Stewart adapter (jsco.net corporate portfolio mode)")
    auth = authority or JOHN_STEWART_AUTHORITY

    city_filter = ",".join(str(i) for i in _JSCO_SCC_CITIES)
    listings: list[dict[str, Any]] = []
    page_num = 1

    from housing_list_search.scraper import SourceFetchError

    while page_num <= 3:  # 67 properties fit in one page of 100; cap defensively
        api_url = f"{_JSCO_API_BASE}/property?city={city_filter}&per_page=100&page={page_num}"
        resp = polite_get(api_url)
        if not resp:
            raise SourceFetchError(
                f"john_stewart/jsco: fetch failed for {api_url}",
                partial=listings,
            )
        try:
            items = resp.json()
        except Exception:
            print("   ⚠️ jsco.net API returned non-JSON — site may have changed")
            raise SourceFetchError(
                f"john_stewart/jsco: non-JSON from {api_url}",
                partial=listings,
            )
        if not isinstance(items, list) or not items:
            break

        now_iso = _dt.now().isoformat()
        for item in items:
            name = _html.unescape((item.get("title") or {}).get("rendered", "")).strip()
            if not name:
                continue
            city_ids = item.get("city") or []
            city = next((_JSCO_SCC_CITIES[i] for i in city_ids if i in _JSCO_SCC_CITIES), "")
            modified = (item.get("modified") or "")[:10]

            listings.append(
                {
                    "authority": auth,
                    "property_name": name,
                    "address": f"{city}, CA" if city else "",
                    "url": item.get("link") or "",
                    "status": "Check with property",
                    "notes": (
                        f"John Stewart managed property in {city or 'Santa Clara County'}"
                        + (f" | vendor page last updated {modified}" if modified else "")
                    ),
                    "unit_types": "Varies",
                    "confidence": "high",
                    "last_seen": now_iso,
                    "first_seen": now_iso,
                    "source": "john_stewart:jsco_portfolio",
                    "source_url": api_url.split("&page=")[0],
                    "expires_at": "",
                }
            )

        if len(items) < 100:
            break
        page_num += 1

    print(f"   → jsco.net portfolio: {len(listings)} Santa Clara County properties")
    return listings


# =============================================================================
# PUBLIC API
# =============================================================================


def scrape_john_stewart(url: str, *, authority: str = "") -> list[dict[str, Any]]:
    """
    Primary entry point for the John Stewart Company adapter.

    This function inspects the incoming URL and dispatches to the appropriate
    parser:

    - If the URL is the SCCHA custom properties directory front-end
      (scchousingauthority.org/.../properties-list/), it uses the high-quality
      structured card parser.
    - For any other URL (typically direct jscosccha.com pages), it falls back
      to the direct vendor site parser.

    The optional `authority` (from TargetContext) is preferred and passed
    through; callers from dispatch supply the TARGETS.md authority. A module
    constant provides a fallback for direct use. Downstream canonical_authority()
    in listing.py ensures stable identity keys across descriptive variants.
    """
    lower_url = url.lower()
    auth = authority or JOHN_STEWART_AUTHORITY

    # SCCHA custom front-end (special structured page)
    if "properties-list" in lower_url and "scchousingauthority.org" in lower_url:
        return _scrape_sccha_directory(url, authority=auth)

    # Corporate portfolio via WordPress REST API (jsco.net)
    if "jsco.net" in lower_url:
        return _scrape_jsco_portfolio(url, authority=auth)

    # Any direct page on the John Stewart platform
    if "jscosccha.com" in lower_url or "jscosccha" in lower_url:
        return _scrape_direct_john_stewart(url, authority=auth)

    # Fallback — try the direct parser anyway (some links may be relative or mirrored)
    return _scrape_direct_john_stewart(url, authority=auth)


# =============================================================================
# USAGE AS TEMPLATE FOR FUTURE ONE-OFF ADAPTERS
# =============================================================================
#
# Copy this file as the starting point when you discover a new municipality
# with its own custom listing page that is not a standard public vendor portal.
#
# Recommended steps:
#   1. Name the new file after the real backend tool/vendor (not the city).
#   2. Implement a public scrape_<vendor>(url) function.
#   3. Add URL detection at the top of the public function.
#   4. Write a dedicated parser for the custom front-end (usually easy wins).
#   5. Keep or improve the direct backend parser.
#   6. Document the relationship in the module docstring exactly like above.
#   7. Wire the new URL pattern into dispatch.py (and update TARGETS.md).
#   8. Add the new source to the deduplication logic if it overlaps with
#      existing sources.
#
# Following this pattern keeps the total number of adapters low and makes
# future maintenance far more tractable.
# =============================================================================
