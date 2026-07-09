"""Playwright navigation URL policy and per-host throttle."""

from unittest.mock import MagicMock

import pytest

from housing_list_search.playwright_nav import safe_goto, validated_goto_url
from housing_list_search.scraper import URLPolicyError, reset_host_throttle


def test_validated_goto_url_accepts_public_https():
    assert validated_goto_url("https://example.com/housing").startswith("https://")


def test_validated_goto_url_blocks_loopback():
    with pytest.raises(URLPolicyError):
        validated_goto_url("http://127.0.0.1/secret")


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
