"""Browser implementation behind the Access seam (private to access.py).

Do not import this module from adapters, extraction, or pipeline.
Use ``housing_list_search.access`` (browser_page, safe_goto, …) instead (#1060).

#761 / #769 / #987: process-wide lock serializes Playwright under parallel
target workers; one browser reused until shutdown_playwright().
"""

from __future__ import annotations

import atexit
import logging
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from housing_list_search.scraper import (
    URLPolicyError,
    mark_host_fetched,
    validate_http_url,
    wait_for_host,
)

logger = logging.getLogger(__name__)

DEFAULT_PLAYWRIGHT_DELAY = 3

# Serialize all Playwright use under parallel target workers (#761)
_PLAYWRIGHT_LOCK = threading.RLock()
_pw_instance: Any = None
_browser: Any = None
_launch_count = 0
_page_count = 0


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


def _ensure_browser():
    """Start Chromium once per process under the Playwright lock."""
    global _pw_instance, _browser, _launch_count
    if _browser is not None:
        return _browser
    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright not installed. pip install playwright && playwright install chromium"
        ) from exc

    logger.info("[playwright] launching shared Chromium (first use this process)")
    _pw_instance = sync_playwright().start()
    _browser = _pw_instance.chromium.launch(headless=True)
    _launch_count += 1
    return _browser


def shutdown_playwright() -> None:
    """Close shared browser (call at end of RunPipeline / tests)."""
    global _pw_instance, _browser
    with _PLAYWRIGHT_LOCK:
        if _browser is not None:
            try:
                _browser.close()
            except Exception as exc:
                logger.debug("browser.close: %s", exc)
            _browser = None
        if _pw_instance is not None:
            try:
                _pw_instance.stop()
            except Exception as exc:
                logger.debug("playwright.stop: %s", exc)
            _pw_instance = None
        logger.info(
            "[playwright] shutdown (launches=%d pages_opened=%d)",
            _launch_count,
            _page_count,
        )


atexit.register(shutdown_playwright)


def playwright_stats() -> dict[str, int]:
    """Test/ops telemetry: how many Chromium launches and pages this process."""
    return {"launches": _launch_count, "pages": _page_count}


def reset_playwright_for_tests() -> None:
    """Close shared browser and zero counters (unit tests)."""
    global _launch_count, _page_count
    shutdown_playwright()
    with _PLAYWRIGHT_LOCK:
        _launch_count = 0
        _page_count = 0


@contextmanager
def browser_page(
    *,
    extra_http_headers: dict[str, str] | None = None,
    viewport: dict[str, int] | None = None,
    locale: str = "en-US",
    timezone_id: str = "America/Los_Angeles",
    **context_kwargs: Any,
) -> Iterator[Any]:
    """Yield a new page from the shared browser; serializes under process lock.

    Only one caller holds the lock at a time — safe under HLS_MAX_TARGET_WORKERS.
    Context/page are closed on exit; the browser process stays warm (#769).
    """
    global _page_count
    with _PLAYWRIGHT_LOCK:
        browser = _ensure_browser()
        ctx_kwargs: dict[str, Any] = {
            "locale": locale,
            "timezone_id": timezone_id,
            **context_kwargs,
        }
        if extra_http_headers:
            ctx_kwargs["extra_http_headers"] = extra_http_headers
        if viewport:
            ctx_kwargs["viewport"] = viewport
        context = browser.new_context(**ctx_kwargs)
        page = context.new_page()
        _page_count += 1
        try:
            yield page
        finally:
            try:
                context.close()
            except Exception as exc:
                logger.debug("context.close: %s", exc)
