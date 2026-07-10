"""Playwright navigation URL policy, shared browser pool, per-host throttle."""

from unittest.mock import MagicMock

import pytest

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
from housing_list_search.scraper import URLPolicyError, reset_host_throttle


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


def test_playwright_response_url_allowed():
    assert playwright_response_url_allowed("https://housing.sanjoseca.gov/api/x") is True
    assert playwright_response_url_allowed("http://169.254.169.254/") is False
    assert playwright_response_url_allowed("data:application/json,{}") is False


def test_attach_egress_policy_aborts_blocked_request():
    """Route handler aborts policy-blocked XHR without continue()."""
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
    handler(route)
    route.abort.assert_called()
    route.continue_.assert_not_called()

    route2 = MagicMock()
    route2.request.url = "https://example.com/xhr.json"
    route2.request.is_navigation_request = lambda: False
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
        return pn._browser

    monkeypatch.setattr(pn, "_ensure_browser", ensure_once)
    with browser_page():
        pass
    with browser_page():
        pass
    assert len(launches) == 1
    assert playwright_stats()["pages"] == 2
