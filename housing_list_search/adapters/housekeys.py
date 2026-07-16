# adapters/housekeys.py
"""
HouseKeys Adapter — Delegated BMR Administrator Pattern (First-Class)

HouseKeys (housekeys24.com) is a third-party platform used by several Bay Area cities
(including the City of Milpitas) to manage Below Market Rate (BMR) homeownership
opportunities via registration + weighted lottery.

This is the reference adapter for "notification/registration portal" style delegated
administrators (similar in spirit to the Rise Housing pattern in gis_extraction).

IMPORTANT REALITY
-----------------
- This is a **registration + notification portal**, not a publicly scrapeable list of units.
- Prospective applicants must create an account and request a city-specific Application ID.
- Opportunities (mostly resales) are announced via weighted lottery when they become available.
- The city (e.g. Milpitas Office of Housing) monitors compliance but does **not** operate
  a public waitlist or publish current openings in HTML/JSON.

Design decision: We return one high-value, high-confidence "registration record" that
directs humans to the real actionable entry point instead of emitting noise or empty results.

Scope & Guardrails
------------------
In Scope:
- Returning a clear, actionable pointer to the official registration flow for the
  authority named in TARGETS.md.
- Preserving administrator context (URL, contact) when supplied via the target row.
- Graceful handling when the site returns 403/404 or is heavily JS-protected.

Out of Scope:
- Attempting to scrape behind login walls or simulate account creation.
- Extracting individual property waitlists or lottery results (they are not public).
- Hunting for staff contacts not published on the target page.

Known Low-Value Patterns
------------------------
- Trying to screen-scrape the public landing page for "current openings" — these
  pages almost always require authentication to see real data.
- This adapter explicitly documents the limitation so future agents do not waste
  effort on it.

PATTERN FOR NEW ONE-OFF ADAPTERS (Delegated Notification Portals)
-----------------------------------------------------------------
When you encounter another city that has outsourced its BMR applicant interface to
a third-party registration/lottery system (HouseKeys, or similar vendors):
1. Add a row in TARGETS.md with the city's public housing page + the real
   registration URL in Administrator / Administrator URL columns if known.
2. Extend the routing in cli.py (or better, in a future central dispatcher) to
   call this adapter (or a generalized version) when the measures or URL indicate
   a HouseKeys-style portal.
3. Keep the output shape compatible with HousingRecord / the common dict used
   by all adapters.
4. Document the specific city quirks in the Notes column of TARGETS.md.

This keeps the skill location-agnostic and prevents one-off city files from
proliferating.
"""

import logging

from housing_list_search.access import polite_get

logger = logging.getLogger(__name__)


def is_housekeys_url(url: str) -> bool:
    return "housekeys" in url.lower()


def scrape_housekeys(authority: str, url: str, admin_url: str = ""):
    """
    Returns a minimal, actionable record directing users to register with HouseKeys
    for the specific city's BMR ownership program.
    """
    print(f"🧩 Running HouseKeys adapter on {url} (delegated administrator)")

    # Attempt to fetch the city page for confirmation, but do NOT gate the
    # registration record on its success. City pages (mountainview.gov, etc.)
    # can be WAF-blocked or temporarily down; the HouseKeys subdomain in
    # admin_url is the real actionable entry point and is independent of the
    # city site. A 403 from the city should not suppress a valid record.
    polite_get(url)  # fire-and-forget; result intentionally ignored

    # We deliberately do not do deep scraping here.
    # The real data lives behind account registration + lotteries.
    # The highest-value thing we can return is a clear pointer.

    # Use the city-specific HouseKeys subdomain from TARGETS.md when available;
    # fall back to the Milpitas reference instance only as a last resort.
    registration_url = admin_url if admin_url else "https://www.housekeys24.com/"

    notes = (
        f"Below Market Rate (BMR) ownership opportunities for {authority} are administered "
        f"through the HouseKeys portal. Create an account and request an Application ID for "
        f"the {authority} program to receive notifications of future lotteries. "
        f"The city monitors the program but does not maintain a public waitlist."
    )

    from datetime import datetime as _dt

    now_iso = _dt.now().isoformat()

    record = {
        "authority": authority,
        "property_name": f"{authority} BMR Homeownership Program (via HouseKeys)",
        "url": registration_url,
        "status": "Registration Required",
        "deadline": "",
        "income_limits": "Varies by program (typically Low / Moderate income)",
        "unit_types": "Varies (resales + new construction)",
        "eligibility_flags": ["below_market_rate", "first_time_homebuyer", "income_qualified"],
        "notes": notes,
        "administrator": "HouseKeys",
        "administrator_url": registration_url,
        "application_url": registration_url,
        "confidence": 0.95,
        "source_url": url,
        # Freshness metadata (0.8.2+)
        "last_seen": now_iso,
        "first_seen": now_iso,
        "source": f"housekeys:{authority.lower().replace(' ', '_')}",
        "expires_at": "",
    }

    logger.info(
        f"HouseKeys is a registration-based portal. "
        f"Users must sign up at {registration_url} for {authority} opportunities. "
        f"No public unit-level list is available for scraping."
    )

    print(
        "   → HouseKeys adapter produced 1 registration record (this is the actionable public entry point)"
    )
    return [record]


def run(ctx) -> list:
    """Adapter port: TargetContext → records (dispatch Handler)."""
    return scrape_housekeys(
        ctx.authority,
        ctx.url,
        admin_url=ctx.administrator_url,
    )
