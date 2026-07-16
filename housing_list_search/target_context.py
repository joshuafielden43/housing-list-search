"""
target_context.py — TargetContext for the Adapter port.

Dispatch builds one TargetContext per Target row; every platform Adapter
implements ``run(ctx: TargetContext) -> list[records]``. No lambda peel.
"""

from __future__ import annotations

from dataclasses import dataclass, field


@dataclass
class TargetContext:
    """Inputs for scraping one Target (authority + URL + admin fields + measures)."""

    authority: str
    url: str
    measures: set[str] = field(default_factory=set)
    administrator: str = ""
    administrator_url: str = ""
    administrator_phone: str = ""
    administrator_contact: str = ""
    notes: str = ""
