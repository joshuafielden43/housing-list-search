"""Playwright navigation helpers — same outbound policy as polite_get."""

from __future__ import annotations

from housing_list_search.url_policy import validate_http_url


def validated_goto_url(url: str) -> str:
    """Validate a URL before Playwright page.goto(). Raises URLPolicyError on violation."""
    return validate_http_url(url, resolve_dns=True)


def safe_goto(page, url: str, **kwargs) -> None:
    """page.goto() after outbound URL policy check."""
    page.goto(validated_goto_url(url), **kwargs)
