"""Filter helpers for selective --run processing."""

from __future__ import annotations

from typing import Any


def filter_targets_by_authority(
    targets: list[dict[str, Any]],
    needle: str,
) -> list[dict[str, Any]]:
    """
    Return targets whose authority contains needle (case-insensitive).

    Empty needle returns all targets unchanged.
    """
    if not needle or not needle.strip():
        return targets
    key = needle.strip().lower()
    return [t for t in targets if key in (t.get("authority") or "").lower()]