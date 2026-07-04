"""
validated_zero.py — human-confirmed empty inventory metadata (ADR-0003).

Validated Zero state is curated in TARGETS.md beside each target row.
"""

from __future__ import annotations

import re
from datetime import date
from typing import Any, Literal

ValidatedZeroStatus = Literal["none", "current", "due"]

_ISO_DATE_RE = re.compile(r"(\d{4}-\d{2}-\d{2})")


def parse_validated_zero_date(raw: str) -> date | None:
    """Extract the first ISO date (YYYY-MM-DD) from a TARGETS.md field."""
    match = _ISO_DATE_RE.search((raw or "").strip())
    if not match:
        return None
    try:
        return date.fromisoformat(match.group(1))
    except ValueError:
        return None


def validated_zero_status(
    target: dict[str, Any],
    *,
    today: date | None = None,
) -> ValidatedZeroStatus:
    """
    Return validation state for a target row.

    - none: no Validated Zero metadata
    - current: validated and review date not yet elapsed
    - due: validated but review window has elapsed (reverify)
    """
    today = today or date.today()
    validated_on = parse_validated_zero_date(target.get("validated_zero") or "")
    review_due = parse_validated_zero_date(target.get("validated_zero_review_due") or "")
    if not validated_on or not review_due:
        return "none"
    if today <= review_due:
        return "current"
    return "due"


def has_current_validated_zero(
    target: dict[str, Any],
    *,
    today: date | None = None,
) -> bool:
    """True when a current Validated Zero suppresses Suspicious Zero for this target."""
    return validated_zero_status(target, today=today) == "current"


def find_reverification_due(
    targets: list[dict[str, Any]],
    *,
    today: date | None = None,
) -> list[str]:
    """Authorities whose Validated Zero review window has elapsed."""
    today = today or date.today()
    due: list[str] = []
    for target in targets:
        authority = (target.get("authority") or "").strip()
        if not authority:
            continue
        if validated_zero_status(target, today=today) == "due":
            due.append(authority)
    return sorted(due)
