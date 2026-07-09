"""
access.py — outbound Access seam (#784 / #796).

Single import surface for "look at a link" and "open a page":

  from housing_list_search.access import polite_get, polite_post, browser_page, safe_goto

Implementation lives in scraper.py (HTTP) and playwright_nav.py (browser).
Adapters and extraction should prefer this module over reaching into scraper
or playwright_nav directly when adding new call sites.
"""

from __future__ import annotations

from housing_list_search.playwright_nav import (
    browser_page,
    playwright_stats,
    reset_playwright_for_tests,
    safe_goto,
    shutdown_playwright,
    validated_goto_url,
)
from housing_list_search.scraper import (
    DEFAULT_MAX_RESPONSE_BYTES,
    USER_AGENT,
    URLPolicyError,
    is_allowed_by_robots,
    polite_get,
    polite_post,
    require_response,
    reset_host_throttle,
    validate_http_url,
)

__all__ = [
    "DEFAULT_MAX_RESPONSE_BYTES",
    "USER_AGENT",
    "URLPolicyError",
    "browser_page",
    "is_allowed_by_robots",
    "playwright_stats",
    "polite_get",
    "polite_post",
    "require_response",
    "reset_host_throttle",
    "reset_playwright_for_tests",
    "safe_goto",
    "shutdown_playwright",
    "validate_http_url",
    "validated_goto_url",
]
