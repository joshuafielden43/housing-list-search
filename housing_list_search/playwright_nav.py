"""Playwright navigation helpers — same outbound policy and throttle as polite_get."""

from __future__ import annotations

from housing_list_search.scraper import mark_host_fetched, validate_http_url, wait_for_host

DEFAULT_PLAYWRIGHT_DELAY = 3


def validated_goto_url(url: str) -> str:
    """Validate a URL before Playwright page.goto(). Raises URLPolicyError on violation."""
    return validate_http_url(url, resolve_dns=True)


def safe_goto(page, url: str, *, delay: int = DEFAULT_PLAYWRIGHT_DELAY, **kwargs) -> None:
    """page.goto() after outbound URL policy check and per-host throttle."""
    validated = validated_goto_url(url)
    try:
        wait_for_host(validated, delay)
        page.goto(validated, **kwargs)
    finally:
        mark_host_fetched(validated)
