"""Unit tests for TARGETS.md registry sanitization."""

from __future__ import annotations

from housing_list_search.registry import sanitize_target


class TestSanitizeTarget:
    def test_administrator_url_requires_http_scheme(self):
        cleaned = sanitize_target({
            "authority": "City of Test",
            "url": "https://example.gov/housing",
            "administrator_url": "javascript:alert(1)",
        })
        assert cleaned["administrator_url"] == ""

    def test_valid_administrator_url_preserved(self):
        cleaned = sanitize_target({
            "authority": "City of Test",
            "url": "https://example.gov/housing",
            "administrator_url": "https://admin.example.org/",
        })
        assert cleaned["administrator_url"] == "https://admin.example.org/"