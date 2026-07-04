"""Shared HTTP limits and helpers for scraper + robots cache."""

from __future__ import annotations

import requests

USER_AGENT = "HousingListAggregator-Nonprofit-Santa Clara-v1 (contact: joshua@fielden.org)"
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # 20 MiB


def read_bounded_content(resp: requests.Response, max_bytes: int) -> bytes | None:
    """Read response body up to max_bytes. Returns None if the cap is exceeded."""
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            resp.close()
            return None
        chunks.append(chunk)
    return b"".join(chunks)
