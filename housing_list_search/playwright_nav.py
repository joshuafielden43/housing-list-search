"""Playwright navigation helpers — same outbound policy and throttle as polite_get."""

from __future__ import annotations

import logging

from housing_list_search.scraper import (
    URLPolicyError,
    mark_host_fetched,
    validate_http_url,
    wait_for_host,
)

logger = logging.getLogger(__name__)

DEFAULT_PLAYWRIGHT_DELAY = 3


def validated_goto_url(url: str) -> str:
    """Validate a URL before Playwright page.goto(). Raises URLPolicyError on violation."""
    return validate_http_url(url, resolve_dns=True)


def safe_goto(page, url: str, *, delay: int = DEFAULT_PLAYWRIGHT_DELAY, **kwargs) -> None:
    """page.goto() after outbound URL policy check and per-host throttle.

    After navigation, re-validates ``page.url`` so a redirect chain that lands
    on a private / metadata / disallowed host is rejected (Playwright follows
    redirects internally; the initial URL check alone is not enough).
    """
    validated = validated_goto_url(url)
    try:
        wait_for_host(validated, delay)
        page.goto(validated, **kwargs)
        final_url = getattr(page, "url", None) or ""
        if final_url:
            try:
                validate_http_url(final_url, resolve_dns=True)
            except URLPolicyError as exc:
                logger.warning(
                    "Playwright navigation landed on policy-blocked URL %s (started %s): %s",
                    final_url,
                    validated,
                    exc,
                )
                raise URLPolicyError(
                    f"Playwright redirect landed on disallowed URL {final_url!r}: {exc}"
                ) from exc
    finally:
        mark_host_fetched(validated)
