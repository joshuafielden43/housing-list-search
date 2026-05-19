"""
Conservative link scoring for discovery.

Designed for high precision (Option A). Better to miss a few good links
than to recommend noise that wastes human review time.
"""

from __future__ import annotations
from dataclasses import dataclass
from urllib.parse import urlparse
import re


@dataclass
class LinkCandidate:
    url: str
    text: str
    score: int
    reason: str
    suggested_category: str = "Unknown"


# Strong positive signals for housing opportunity pages (especially Gilroy-style)
POSITIVE_PATTERNS = [
    (r"/797/Affordable-Apartment", "Primary affordable rentals page", 95, "Rentals"),
    (r"/748/Below-Market-Rate", "BMR Home Ownership", 90, "Homeownership"),
    (r"/289/Homebuyer-Assistance", "Homebuyer assistance", 85, "Homeownership"),
    (r"affordable.*apartment|affordable.*rental", "Affordable apartment/rental keywords", 75, "Rentals"),
    (r"below.?market|bmr", "Below Market Rate", 70, "Homeownership"),
    (r"waitlist|lottery|interest.?list", "Waitlist / Lottery language", 65, "Rentals"),
    (r"unhoused|homeless|emergency.?housing", "Unhoused / Emergency housing", 60, "Unhoused"),
    (r"rental.?assistance", "Rental assistance", 55, "Rentals"),
]

# Strong negative signals – we want to be very conservative
NEGATIVE_PATTERNS = [
    (r"youtube|youtu\.be", "YouTube video", -100),
    (r"workshop|recorded|presentation|flyer", "Workshop / presentation material", -80),
    (r"developer.?roundtable|event", "Event / roundtable", -70),
    (r"block.?grant|plha|grant.?fund", "Grant program overview (not opportunity list)", -60),
    (r"tenant.?landlord|fair.?housing|rights", "Tenant rights / fair housing (important but not opportunity list)", -50),
    (r"DocumentCenter/View", "Generic document center link (often slides/minutes)", -40),
]


def score_link(url: str, link_text: str) -> LinkCandidate:
    """
    Conservative scoring function.
    Returns a LinkCandidate with score and explanation.
    """
    url_lower = url.lower()
    text_lower = link_text.lower().strip()
    combined = f"{url_lower} {text_lower}"

    score = 0
    reasons = []

    # Check positive signals
    for pattern, reason, points, category in POSITIVE_PATTERNS:
        if re.search(pattern, combined, re.I):
            score += points
            reasons.append(f"+{points} {reason}")
            suggested_category = category

    # Check negative signals (these can heavily penalize)
    for pattern, reason, penalty in NEGATIVE_PATTERNS:
        if re.search(pattern, combined, re.I):
            score += penalty
            reasons.append(f"{penalty} {reason}")

    # Base score for any internal housing-related page
    if "housing" in combined or "community" in combined:
        score += 10

    # Penalize very generic or root-level pages
    parsed = urlparse(url)
    if parsed.path in ("", "/", "/279/Housing-and-Community-Services"):
        score -= 30
        reasons.append("-30 Generic hub/root page")

    # Final reason string
    reason_str = " | ".join(reasons) if reasons else "No strong signals"

    # Determine suggested category (default to Unknown if nothing matched)
    if 'suggested_category' not in locals():
        suggested_category = "Unknown"

    return LinkCandidate(
        url=url,
        text=link_text,
        score=score,
        reason=reason_str,
        suggested_category=suggested_category,
    )


def is_worth_considering(candidate: LinkCandidate, min_score: int = 40) -> bool:
    """
    Conservative gate: only recommend links that clear a reasonably high bar.
    """
    return candidate.score >= min_score
