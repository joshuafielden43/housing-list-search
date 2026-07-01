"""
Per-host robots.txt cache for housing-list-search.

Avoids re-fetching robots.txt on every polite_get to the same origin.
"""

from __future__ import annotations

import logging
import threading
import urllib.robotparser
from dataclasses import dataclass
from typing import Optional

import requests

from housing_list_search.http_limits import USER_AGENT, read_bounded_content

logger = logging.getLogger(__name__)

_ROBOTS_CACHE: dict[str, "RobotsEntry"] = {}
_CACHE_LOCK = threading.Lock()


@dataclass
class RobotsEntry:
    """Cached robots.txt evaluation state for one origin."""

    parser: Optional[urllib.robotparser.RobotFileParser]
    treat_as_allowed: bool


def clear_robots_cache() -> None:
    """Reset cache (tests)."""
    with _CACHE_LOCK:
        _ROBOTS_CACHE.clear()


def get_robots_entry(base: str, robots_url: str) -> RobotsEntry:
    """Return cached robots entry for ``base`` (scheme://host), fetching on miss."""
    with _CACHE_LOCK:
        cached = _ROBOTS_CACHE.get(base)
        if cached is not None:
            return cached

    entry = _fetch_robots_entry(robots_url)

    with _CACHE_LOCK:
        _ROBOTS_CACHE[base] = entry
        return entry


def _fetch_robots_entry(robots_url: str) -> RobotsEntry:
    try:
        resp = requests.get(
            robots_url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            stream=True,
        )
        content = read_bounded_content(resp, 256 * 1024)
        if content is None:
            logger.warning(
                "robots.txt response exceeded size cap for %s — treating as unreachable (allowed)",
                robots_url,
            )
            return RobotsEntry(parser=None, treat_as_allowed=True)

        if resp.status_code in (401, 403):
            logger.warning(
                "robots.txt returned HTTP %d for %s — treating as unreachable (allowed). "
                "If this is a WAF block on the robots fetch itself, document in TARGETS.md.",
                resp.status_code,
                robots_url,
            )
            return RobotsEntry(parser=None, treat_as_allowed=True)
        if resp.status_code >= 400:
            return RobotsEntry(parser=None, treat_as_allowed=True)

        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(content.decode("utf-8", errors="replace").splitlines())
        return RobotsEntry(parser=rp, treat_as_allowed=False)

    except Exception as exc:
        logger.debug("robots.txt fetch for %s failed (%s) — treating as allowed", robots_url, exc)
        return RobotsEntry(parser=None, treat_as_allowed=True)