"""Playwright navigation URL policy, shared browser pool, per-host throttle."""

import threading
from unittest.mock import MagicMock

import pytest

from housing_list_search.access import URLPolicyError, reset_host_throttle
from housing_list_search.playwright_nav import (
    assert_playwright_egress_url,
    attach_playwright_egress_policy,
    browser_page,
    playwright_response_url_allowed,
    playwright_stats,
    reset_playwright_for_tests,
    safe_goto,
    validated_goto_url,
)


def test_validated_goto_url_accepts_public_https():
    assert validated_goto_url("https://example.com/housing").startswith("https://")


def test_validated_goto_url_blocks_loopback():
    with pytest.raises(URLPolicyError):
        validated_goto_url("http://127.0.0.1/secret")


def test_assert_playwright_egress_blocks_metadata_and_loopback():
    """#775: XHR/subresource URLs use the same host policy as HTTP."""
    with pytest.raises(URLPolicyError):
        assert_playwright_egress_url("http://169.254.169.254/latest/meta-data/")
    with pytest.raises(URLPolicyError):
        assert_playwright_egress_url("http://127.0.0.1/admin")
    assert assert_playwright_egress_url("https://example.com/api.json").startswith("https://")


def test_assert_playwright_xhr_dns_to_private_blocked(monkeypatch):
    """#1082: data-carrying types resolve DNS — public name → RFC1918 is blocked."""
    monkeypatch.setattr(
        "housing_list_search.scraper.socket.getaddrinfo",
        lambda *a, **k: [(None, None, None, None, ("10.0.0.5", 0))],
    )
    with pytest.raises(URLPolicyError, match="non-public"):
        assert_playwright_egress_url(
            "https://evil.example.org/api",
            resource_type="xhr",
        )


def test_assert_playwright_image_skips_dns(monkeypatch):
    """Static assets stay host-policy only (no DNS) for cost."""
    called = {"dns": False}

    def boom(*_a, **_k):
        called["dns"] = True
        raise AssertionError("DNS should not run for images")

    monkeypatch.setattr("housing_list_search.scraper.socket.getaddrinfo", boom)
    # Public hostname + image type — no DNS call
    assert assert_playwright_egress_url(
        "https://cdn.example.org/logo.png",
        resource_type="image",
    ).startswith("https://")
    assert called["dns"] is False


def test_playwright_response_url_allowed(monkeypatch):
    monkeypatch.setattr(
        "housing_list_search.playwright_nav.is_safe_http_url",
        lambda url, **k: "169.254" not in url and not str(url).startswith("data:"),
    )
    assert playwright_response_url_allowed("https://housing.sanjoseca.gov/api/x") is True
    assert playwright_response_url_allowed("http://169.254.169.254/") is False
    assert playwright_response_url_allowed("data:application/json,{}") is False


def test_attach_egress_policy_aborts_blocked_request(monkeypatch):
    """Route handler aborts policy-blocked XHR without continue()."""
    monkeypatch.setattr(
        "housing_list_search.playwright_nav.validate_http_url",
        lambda url, **k: url
        if "127.0.0.1" not in url and "169.254" not in url
        else (_ for _ in ()).throw(URLPolicyError("blocked")),
    )
    page = MagicMock()
    handlers: list = []

    def capture_route(pattern, handler):
        handlers.append((pattern, handler))

    page.route.side_effect = capture_route
    attach_playwright_egress_policy(page)
    assert handlers
    _, handler = handlers[0]

    route = MagicMock()
    route.request.url = "http://127.0.0.1/secret"
    route.request.is_navigation_request = lambda: False
    route.request.resource_type = "xhr"
    handler(route)
    route.abort.assert_called()
    route.continue_.assert_not_called()

    route2 = MagicMock()
    route2.request.url = "https://example.com/xhr.json"
    route2.request.is_navigation_request = lambda: False
    route2.request.resource_type = "xhr"
    handler(route2)
    route2.continue_.assert_called()


def test_safe_goto_waits_for_host_throttle(monkeypatch):
    reset_host_throttle()
    calls: list[str] = []

    def fake_wait(url, delay):
        calls.append(f"wait:{url}:{delay}")

    def fake_mark(url):
        calls.append(f"mark:{url}")

    page = MagicMock()
    page.url = "https://example.com/listings"
    monkeypatch.setattr("housing_list_search.playwright_nav.wait_for_host", fake_wait)
    monkeypatch.setattr("housing_list_search.playwright_nav.mark_host_fetched", fake_mark)
    monkeypatch.setattr(
        "housing_list_search.playwright_nav.validate_http_url",
        lambda url, **_: url,
    )
    monkeypatch.setattr(
        "housing_list_search.playwright_nav.validated_goto_url",
        lambda url: url,
    )

    safe_goto(page, "https://example.com/listings", delay=2)

    assert calls[0] == "wait:https://example.com/listings:2"
    assert calls[-1] == "mark:https://example.com/listings"
    page.goto.assert_called_once()


def test_safe_goto_rejects_final_url_after_redirect(monkeypatch):
    """#1057: final page.url after browser redirect must pass URL policy."""
    page = MagicMock()
    page.url = "http://127.0.0.1/secret"

    monkeypatch.setattr("housing_list_search.playwright_nav.wait_for_host", lambda *a, **k: None)
    monkeypatch.setattr("housing_list_search.playwright_nav.mark_host_fetched", lambda *a, **k: None)
    monkeypatch.setattr(
        "housing_list_search.playwright_nav.validated_goto_url",
        lambda url: url,
    )

    def policy(url, **kwargs):
        if "127.0.0.1" in url:
            raise URLPolicyError("loopback blocked")
        return url

    monkeypatch.setattr("housing_list_search.playwright_nav.validate_http_url", policy)

    with pytest.raises(URLPolicyError, match="disallowed URL"):
        safe_goto(page, "https://example.com/start")
    page.goto.assert_called_once()


def test_safe_goto_accepts_same_public_final_url(monkeypatch):
    page = MagicMock()
    page.url = "https://example.com/listings?page=2"

    monkeypatch.setattr("housing_list_search.playwright_nav.wait_for_host", lambda *a, **k: None)
    monkeypatch.setattr("housing_list_search.playwright_nav.mark_host_fetched", lambda *a, **k: None)
    monkeypatch.setattr(
        "housing_list_search.playwright_nav.validated_goto_url",
        lambda url: url,
    )
    monkeypatch.setattr(
        "housing_list_search.playwright_nav.validate_http_url",
        lambda url, **_: url,
    )

    safe_goto(page, "https://example.com/listings")
    page.goto.assert_called_once()


def test_browser_page_reuses_shared_browser(monkeypatch):
    """#761/#769: one Chromium launch, multiple pages under the process lock."""
    reset_playwright_for_tests()

    fake_browser = MagicMock()
    fake_context = MagicMock()
    fake_page = MagicMock()
    fake_browser.new_context.return_value = fake_context
    fake_context.new_page.return_value = fake_page

    launch_calls = {"n": 0}

    def fake_ensure():
        launch_calls["n"] += 1
        import housing_list_search.playwright_nav as pn

        pn._browser = fake_browser
        pn._owner_ident = __import__("threading").get_ident()
        return fake_browser

    monkeypatch.setattr(
        "housing_list_search.playwright_nav._ensure_browser",
        fake_ensure,
    )

    with browser_page() as p1:
        assert p1 is fake_page
        fake_page.route.assert_called()  # #775 egress policy installed
    with browser_page() as p2:
        assert p2 is fake_page

    # _ensure_browser called twice but we only "launch" once if we set _browser —
    # simulate reuse: second call sees _browser set
    reset_playwright_for_tests()
    import housing_list_search.playwright_nav as pn

    launches = []

    def ensure_once():
        if pn._browser is None:
            launches.append(1)
            pn._browser = fake_browser
            pn._owner_ident = __import__("threading").get_ident()
        return pn._browser

    monkeypatch.setattr(pn, "_ensure_browser", ensure_once)
    with browser_page():
        pass
    with browser_page():
        pass
    assert len(launches) == 1
    assert playwright_stats()["pages"] == 2


def test_ensure_browser_relaunches_on_thread_switch(monkeypatch):
    """ThreadPool workers must not share a greenlet-bound browser."""
    import housing_list_search.playwright_nav as pn

    reset_playwright_for_tests()
    created: list[int] = []

    class FakeBrowser:
        def new_context(self, **kwargs):
            ctx = MagicMock()
            ctx.new_page.return_value = MagicMock()
            return ctx

        def close(self):
            pass

    class FakePw:
        def __init__(self):
            self.chromium = MagicMock()
            self.chromium.launch.return_value = FakeBrowser()

        def stop(self):
            pass

    def fake_sync_playwright():
        class CM:
            def start(self):
                created.append(threading.get_ident())
                return FakePw()

        return CM()

    monkeypatch.setattr(
        "playwright.sync_api.sync_playwright",
        fake_sync_playwright,
        raising=False,
    )
    # Import path used inside _ensure_browser
    import sys
    from types import ModuleType

    fake_mod = ModuleType("playwright.sync_api")
    fake_mod.sync_playwright = fake_sync_playwright
    monkeypatch.setitem(sys.modules, "playwright.sync_api", fake_mod)
    monkeypatch.setitem(sys.modules, "playwright", ModuleType("playwright"))

    # Same thread: one launch
    b1 = pn._ensure_browser()
    b2 = pn._ensure_browser()
    assert b1 is b2
    assert len(created) == 1

    # Simulate another thread by forcing owner mismatch
    pn._owner_ident = threading.get_ident() + 99999
    b3 = pn._ensure_browser()
    assert b3 is not b1
    assert len(created) == 2
    assert pn._owner_ident == threading.get_ident()
    reset_playwright_for_tests()
