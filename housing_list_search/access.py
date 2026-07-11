"""
access.py — deep outbound Access seam (#1060 / #1073).

The only public import surface for HTTP fetch and browser navigation:

  from housing_list_search.access import (
      polite_get, polite_post, require_response,
      SourceFetchError, URLPolicyError,
      browser_page, safe_goto, shutdown_playwright,
      validate_http_url, is_safe_http_url,
  )

Implementation lives in scraper.py (HTTP: policy, robots, throttle, redirects)
and playwright_nav.py (browser pool + safe_goto). Those modules are private —
adapters, extraction, pipeline, and *public* tests must not import them.

White-box unit tests of robots/throttle internals may import scraper.py; that
is testing private implementation, not the Access interface.

Two adapters at this seam (HTTP + browser) justify the depth.
"""

from __future__ import annotations

from housing_list_search.playwright_nav import (
    browser_page,
    playwright_response_url_allowed,
    reset_playwright_for_tests,
    safe_goto,
    shutdown_playwright,
)
from housing_list_search.scraper import (
    DEFAULT_MAX_RESPONSE_BYTES,
    USER_AGENT,
    SourceFetchError,
    URLPolicyError,
    clear_robots_cache,
    is_allowed_by_robots,
    is_safe_http_url,
    polite_get,
    polite_post,
    require_response,
    reset_host_throttle,
    validate_http_url,
)

__all__ = [
    # HTTP fetch
    "DEFAULT_MAX_RESPONSE_BYTES",
    "USER_AGENT",
    "SourceFetchError",
    "URLPolicyError",
    "is_allowed_by_robots",
    "is_safe_http_url",
    "polite_get",
    "polite_post",
    "require_response",
    "validate_http_url",
    # Browser
    "browser_page",
    "playwright_response_url_allowed",
    "safe_goto",
    "shutdown_playwright",
    # Test hooks (reset process-local caches between cases)
    "clear_robots_cache",
    "reset_host_throttle",
    "reset_playwright_for_tests",
]
