"""Playwright navigation URL policy and per-host throttle."""

from unittest.mock import MagicMock

import pytest

from housing_list_search.host_throttle import reset_host_throttle
from housing_list_search.playwright_nav import safe_goto, validated_goto_url
from housing_list_search.url_policy import URLPolicyError


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
    monkeypatch.setattr("housing_list_search.playwright_nav.wait_for_host", fake_wait)
    monkeypatch.setattr("housing_list_search.playwright_nav.mark_host_fetched", fake_mark)

    safe_goto(page, "https://example.com/listings", delay=2)

    assert calls[0] == "wait:https://example.com/listings:2"
    assert calls[-1] == "mark:https://example.com/listings"
    page.goto.assert_called_once()
