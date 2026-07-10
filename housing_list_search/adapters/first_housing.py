"""
First Community Housing Adapter (First-Class, Company-Named)

First Community Housing / FirstHousing (firsthousing.org) is a San José
nonprofit developer with ~20 properties, nearly all in Santa Clara County.
theunitedeffort.org watches 20 of their pages individually.

Data source: the /portfolio page (Wix) renders per-property cards with name,
full street address, office phone, and a leasing email (often @jsco.net —
day-to-day management is John Stewart Company, so these records enrich the
jsco.net portfolio records with direct contacts and win dedupe scoring).
One request covers the whole portfolio.

Public entry point:
    scrape_first_housing(authority, url)
"""

from __future__ import annotations

import logging
import re
from datetime import datetime as _dt
from typing import Any

from bs4 import BeautifulSoup

from housing_list_search.access import polite_get

logger = logging.getLogger(__name__)

PORTFOLIO_URL = "https://www.firsthousing.org/portfolio"

_ADDRESS_RE = re.compile(r"\d{1,5}[\w .'-]+,\s*[A-Z][A-Za-z .]+,\s*CA\s*\d{5}")
_PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]\d{3}[\s.-]\d{4}")
_EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+")


def parse_portfolio(html_text: str, now_iso: str, source_url: str) -> list[dict[str, Any]]:
    """Parse the FirstHousing /portfolio property cards."""
    soup = BeautifulSoup(html_text, "html.parser")
    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for a in soup.find_all("a", href=re.compile(r"^https://www\.firsthousing\.org/[a-z0-9-]+$")):
        href = a["href"]
        if href in seen:
            continue

        # Walk up to the card; a real property card contains a street address.
        card = a
        text = ""
        for _ in range(7):
            card = card.find_parent()
            if card is None:
                break
            text = card.get_text(" ", strip=True)
            if _ADDRESS_RE.search(text):
                break
        addr_m = _ADDRESS_RE.search(text or "")
        if not addr_m:
            continue  # navigation/footer link, not a property card

        address = re.sub(r"\s+", " ", addr_m.group(0)).strip()
        # Wix cards link via "Information" buttons and use the address as the
        # heading — the clean property name lives in the URL slug
        # (/betty-ann-gardens → "Betty Ann Gardens").
        slug = href.rstrip("/").rsplit("/", 1)[-1]
        if slug in {"donate", "career", "careers", "alternativetransportation"}:
            continue
        # Known irregular slugs that don't titlecase into the property name
        _SLUG_NAMES = {"sss": "Second Street Studios"}
        name = _SLUG_NAMES.get(slug, slug.replace("-", " ").title())
        if not name or len(name) < 3:
            continue

        phone_m = _PHONE_RE.search(text)
        email_m = _EMAIL_RE.search(text)

        seen.add(href)
        records.append(
            {
                "authority": "First Community Housing (Santa Clara County portfolio)",
                "property_name": name,
                "address": address,
                "phone": phone_m.group(0) if phone_m else "",
                "email": email_m.group(0) if email_m else "",
                "url": href,
                "status": "Check with property",
                "administrator": "First Community Housing",
                "administrator_url": "https://www.firsthousing.org/",
                "notes": "First Community Housing property (day-to-day management "
                "typically John Stewart Company)",
                "confidence": "high",
                "last_seen": now_iso,
                "first_seen": now_iso,
                "source": "first_housing:portfolio",
                "source_url": source_url,
                "expires_at": "",
            }
        )

    return records


def scrape_first_housing(authority: str = "", url: str = "") -> list[dict[str, Any]]:
    """Public entry point. Single request to the portfolio page."""
    print("🧩 Running First Community Housing adapter (portfolio page)")
    now_iso = _dt.now().isoformat()
    target = url or PORTFOLIO_URL

    from housing_list_search.access import require_response

    resp = require_response(polite_get(target), target, context="first_housing")

    records = parse_portfolio(resp.text, now_iso, target)
    print(f"   → First Community Housing: {len(records)} properties")
    return records
