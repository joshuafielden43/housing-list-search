"""Playwright navigation URL policy."""

import pytest

from housing_list_search.playwright_nav import validated_goto_url
from housing_list_search.url_policy import URLPolicyError


def test_validated_goto_url_accepts_public_https():
    assert validated_goto_url("https://example.com/housing").startswith("https://")


def test_validated_goto_url_blocks_loopback():
    with pytest.raises(URLPolicyError):
        validated_goto_url("http://127.0.0.1/secret")
