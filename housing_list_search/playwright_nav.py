"""Browser implementation behind the Access seam (private to access.py).

Do not import this module from adapters, extraction, or pipeline.
Use ``housing_list_search.access`` (browser_page, safe_goto, …) instead (#1060).

#761 / #769 / #987: process-wide lock serializes Playwright under parallel
target workers so only one page is open at a time (RAM-friendly on 8GB hosts).

Playwright's *sync* API is greenlet/thread-bound: the browser must be used only
on the thread that started ``sync_playwright()``. Under ThreadPoolExecutor,
different workers need different owners — we relaunch Chromium when the owning
thread changes rather than sharing a cross-thread browser (greenlet.error).
"""

from __future__ import annotations

import atexit
import logging
import os
import signal
import subprocess
import threading
from collections.abc import Iterator
from contextlib import contextmanager
from typing import Any

from housing_list_search.scraper import (
    URLPolicyError,
    is_safe_http_url,
    mark_host_fetched,
    validate_http_url,
    wait_for_host,
)

logger = logging.getLogger(__name__)

DEFAULT_PLAYWRIGHT_DELAY = 3

# In-page schemes that are not HTTP egress (do not run URL policy).
_NON_HTTP_SCHEMES = ("data:", "blob:", "about:")

# Serialize all Playwright use under parallel target workers (#761)
_PLAYWRIGHT_LOCK = threading.RLock()
_pw_instance: Any = None
_browser: Any = None
_owner_ident: int | None = None  # threading.get_ident() of browser owner
# PIDs spawned with the current browser (driver + Chromium subtree) for reclaim (#239)
_browser_pids: set[int] = set()
_launch_count = 0
_page_count = 0


def validated_goto_url(url: str) -> str:
    """Validate a URL before Playwright page.goto(). Raises URLPolicyError on violation."""
    return validate_http_url(url, resolve_dns=True)


# Resource types that can carry data or scripts — resolve DNS (#1082).
# Images/fonts/CSS stay host/IP-only so asset storms stay cheap.
_DNS_RESOURCE_TYPES = frozenset(
    {
        "document",
        "xhr",
        "fetch",
        "websocket",
        "eventsource",
        "script",
    }
)


def assert_playwright_egress_url(
    url: str,
    *,
    is_navigation: bool = False,
    resource_type: str = "",
) -> str:
    """Validate a browser request or response URL (#775 / #1082).

    Navigations and data-carrying resource types (document/xhr/fetch/script/…)
    use DNS resolution so a public name that resolves to metadata/RFC1918 is
    blocked. Static assets (image/font/stylesheet) use host/IP policy only.
    Literal private IPs, localhost, and metadata hostnames are always blocked.
    """
    rt = (resource_type or "").lower()
    resolve = is_navigation or rt in _DNS_RESOURCE_TYPES
    return validate_http_url(url, resolve_dns=resolve)


def playwright_response_url_allowed(url: str) -> bool:
    """True if a captured response URL is safe to read (Bloom spy, etc.).

    Uses DNS resolution (#1082) — spies read JSON bodies, not image pixels.
    """
    if not url or str(url).startswith(_NON_HTTP_SCHEMES):
        return False
    return is_safe_http_url(url, resolve_dns=True)


def attach_playwright_egress_policy(page) -> None:
    """Abort Playwright requests to policy-blocked hosts (#775 / #1082).

    Installed automatically by ``browser_page``. Covers XHR/fetch/img after the
    seed navigation — not only the initial ``safe_goto`` URL.
    """

    def handle_route(route) -> None:
        req = route.request
        url = getattr(req, "url", "") or ""
        if str(url).startswith(_NON_HTTP_SCHEMES):
            route.continue_()
            return
        try:
            is_nav = False
            is_nav_fn = getattr(req, "is_navigation_request", None)
            if callable(is_nav_fn):
                is_nav = bool(is_nav_fn())
            resource_type = getattr(req, "resource_type", "") or ""
            assert_playwright_egress_url(
                url, is_navigation=is_nav, resource_type=str(resource_type)
            )
        except URLPolicyError as exc:
            logger.warning("[playwright] blocked egress to %s: %s", url, exc)
            try:
                route.abort("blockedbyclient")
            except TypeError:
                route.abort()
            return
        route.continue_()

    page.route("**/*", handle_route)


def safe_goto(page, url: str, *, delay: int = DEFAULT_PLAYWRIGHT_DELAY, **kwargs) -> None:
    """page.goto() after outbound URL policy check and per-host throttle.

    After navigation, re-validates ``page.url`` so a redirect chain that lands
    on a private / metadata / disallowed host is rejected (Playwright follows
    redirects internally; the initial URL check alone is not enough).
    XHR/subresource egress is filtered by ``attach_playwright_egress_policy``
    on the page (installed by browser_page).
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


def _descendant_pids(root_pid: int | None = None) -> set[int]:
    """Best-effort set of descendant PIDs (macOS/Linux pgrep -P walk)."""
    root = root_pid if root_pid is not None else os.getpid()
    found: set[int] = set()
    frontier = [root]
    while frontier:
        parent = frontier.pop()
        try:
            out = subprocess.check_output(
                ["pgrep", "-P", str(parent)],
                text=True,
                stderr=subprocess.DEVNULL,
            )
        except (subprocess.CalledProcessError, FileNotFoundError, OSError):
            continue
        for line in out.split():
            try:
                pid = int(line)
            except ValueError:
                continue
            if pid not in found and pid != root:
                found.add(pid)
                frontier.append(pid)
    return found


def _kill_pids(pids: set[int], *, reason: str) -> None:
    """SIGTERM then SIGKILL process tree members (cross-thread reclaim #239)."""
    if not pids:
        return
    logger.info("[playwright] reclaiming %d process(es) (%s)", len(pids), reason)
    for pid in sorted(pids):
        try:
            os.kill(pid, signal.SIGTERM)
        except ProcessLookupError:
            continue
        except PermissionError as exc:
            logger.debug("kill(%s) SIGTERM: %s", pid, exc)
    # Brief grace; then force
    for pid in sorted(pids):
        try:
            os.kill(pid, 0)  # still alive?
        except ProcessLookupError:
            continue
        except OSError:
            continue
        try:
            os.kill(pid, signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass


def _drop_browser_refs_unlocked() -> None:
    """Clear process-level browser pointers without close() (may be wrong thread)."""
    global _pw_instance, _browser, _owner_ident, _browser_pids
    _pw_instance = None
    _browser = None
    _owner_ident = None
    _browser_pids = set()


def _abandon_browser_unlocked(*, reason: str) -> None:
    """Drop refs and kill tracked Chromium/driver PIDs (sync close is thread-bound)."""
    global _browser_pids
    pids = set(_browser_pids)
    _drop_browser_refs_unlocked()
    _kill_pids(pids, reason=reason)


def _ensure_browser():
    """Start Chromium on the *current* thread; relaunch if owner thread changed.

    Playwright sync API is not cross-thread. ThreadPool workers that each call
    browser_page must not share one greenlet-bound browser.
    """
    global _pw_instance, _browser, _launch_count, _owner_ident, _browser_pids
    tid = threading.get_ident()
    if _browser is not None and _owner_ident == tid:
        return _browser

    if _browser is not None and _owner_ident != tid:
        # Cross-thread close() is unsafe (greenlet.error). Kill tracked PIDs (#239).
        logger.info(
            "[playwright] thread switch owner=%s → %s; reclaiming prior Chromium "
            "and launching on this thread (sync API is thread-bound)",
            _owner_ident,
            tid,
        )
        _abandon_browser_unlocked(reason=f"thread switch → {tid}")

    try:
        from playwright.sync_api import sync_playwright
    except ImportError as exc:
        raise RuntimeError(
            "Playwright not installed. pip install playwright && playwright install chromium"
        ) from exc

    logger.info("[playwright] launching Chromium (thread=%s)", tid)
    before = _descendant_pids()
    _pw_instance = sync_playwright().start()
    _browser = _pw_instance.chromium.launch(headless=True)
    after = _descendant_pids()
    _browser_pids = after - before
    _owner_ident = tid
    _launch_count += 1
    return _browser


def shutdown_playwright() -> None:
    """Close browser if owned by the current thread (end of RunPipeline / tests).

    If another thread still owns the browser, reclaim via SIGTERM/SIGKILL on
    tracked PIDs rather than leaving orphan Chromiums until process exit (#239).
    """
    global _pw_instance, _browser, _owner_ident, _browser_pids
    with _PLAYWRIGHT_LOCK:
        tid = threading.get_ident()
        if _browser is not None and _owner_ident is not None and _owner_ident != tid:
            logger.info(
                "[playwright] shutdown on thread %s but browser owned by %s — "
                "reclaiming PIDs (cannot close() cross-thread)",
                tid,
                _owner_ident,
            )
            _abandon_browser_unlocked(reason=f"shutdown from thread {tid}")
            logger.info(
                "[playwright] shutdown (launches=%d pages_opened=%d)",
                _launch_count,
                _page_count,
            )
            return

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
        # Same-thread close should have reaped children; kill any stragglers.
        stragglers = set(_browser_pids)
        _browser_pids = set()
        _owner_ident = None
        if stragglers:
            _kill_pids(stragglers, reason="shutdown stragglers")
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
        _drop_browser_refs_unlocked()


@contextmanager
def browser_page(
    *,
    extra_http_headers: dict[str, str] | None = None,
    viewport: dict[str, int] | None = None,
    locale: str = "en-US",
    timezone_id: str = "America/Los_Angeles",
    **context_kwargs: Any,
) -> Iterator[Any]:
    """Yield a new page from the thread-owned browser; serializes under process lock.

    Only one caller holds the lock at a time — safe under HLS_MAX_TARGET_WORKERS
    and keeps peak Chromium count to one active page. The browser is bound to the
    calling thread; another worker re-launches on first use (no cross-thread greenlet).
    Context/page are closed on exit; the browser stays warm for the same thread.
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
        attach_playwright_egress_policy(page)
        _page_count += 1
        try:
            yield page
        finally:
            try:
                context.close()
            except Exception as exc:
                logger.debug("context.close: %s", exc)
