"""
access.py — deep outbound Access seam (#1060).

The only public import surface for "look at a link" and "open a page":

  from housing_list_search.access import (
      polite_get, polite_post, require_response, SourceFetchError,
      browser_page, safe_goto, validate_http_url, URLPolicyError,
  )

Implementation lives in scraper.py (HTTP: policy, robots, throttle, redirects)
and playwright_nav.py (browser pool + safe_goto). Those modules are private
to Access — adapters, extraction, and pipeline must not import them directly.

Two adapters at this seam (HTTP + browser) justify the depth; callers learn one
interface and get both.
"""

from __future__ import annotations

from housing_list_search.playwright_nav import (
    DEFAULT_PLAYWRIGHT_DELAY,
    attach_playwright_egress_policy,
    browser_page,
    playwright_response_url_allowed,
    playwright_stats,
    reset_playwright_for_tests,
    safe_goto,
    shutdown_playwright,
    validated_goto_url,
)
from housing_list_search.scraper import (
    DEFAULT_MAX_RESPONSE_BYTES,
    USER_AGENT,
    SourceFetchError,
    URLPolicyError,
    is_allowed_by_robots,
    is_safe_http_url,
    mark_host_fetched,
    polite_get,
    polite_post,
    read_bounded_content,
    require_response,
    reset_host_throttle,
    validate_http_url,
    wait_for_host,
)

__all__ = [
    "DEFAULT_MAX_RESPONSE_BYTES",
    "DEFAULT_PLAYWRIGHT_DELAY",
    "USER_AGENT",
    "URLPolicyError",
    "SourceFetchError",
    "attach_playwright_egress_policy",
    "browser_page",
    "is_allowed_by_robots",
    "is_safe_http_url",
    "mark_host_fetched",
    "playwright_response_url_allowed",
    "playwright_stats",
    "polite_get",
    "polite_post",
    "read_bounded_content",
    "require_response",
    "reset_host_throttle",
    "reset_playwright_for_tests",
    "safe_goto",
    "shutdown_playwright",
    "validate_http_url",
    "validated_goto_url",
    "wait_for_host",
]
