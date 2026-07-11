"""Unit tests for outbound URL policy."""

from __future__ import annotations

from unittest.mock import patch

import pytest

from housing_list_search.access import URLPolicyError, is_safe_http_url, validate_http_url


class TestValidateHttpUrl:
    def test_allows_public_https_url(self):
        with patch("housing_list_search.scraper._check_resolved_addresses"):
            assert validate_http_url("https://housing.sanjoseca.gov/listings") == (
                "https://housing.sanjoseca.gov/listings"
            )

    def test_rejects_file_scheme(self):
        with pytest.raises(URLPolicyError, match="scheme"):
            validate_http_url("file:///etc/passwd")

    def test_rejects_localhost(self):
        with pytest.raises(URLPolicyError, match="Blocked hostname"):
            validate_http_url("http://localhost/listings")

    def test_rejects_private_ip_literal(self):
        with pytest.raises(URLPolicyError, match="Non-public IP"):
            validate_http_url("http://192.168.1.1/admin")

    def test_rejects_metadata_ip(self):
        with pytest.raises(URLPolicyError, match="169.254"):
            validate_http_url("http://169.254.169.254/latest/meta-data/")

    def test_rejects_dns_to_private_ip(self):
        with patch(
            "housing_list_search.scraper.socket.getaddrinfo",
            return_value=[(None, None, None, None, ("10.0.0.5", 0))],
        ):
            with pytest.raises(URLPolicyError, match="non-public address"):
                validate_http_url("https://evil.example.org/")

    def test_is_safe_http_url_returns_false_without_raising(self):
        assert is_safe_http_url("http://127.0.0.1/") is False
