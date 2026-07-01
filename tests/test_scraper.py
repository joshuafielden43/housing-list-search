"""
Unit tests for scraper.py robots.txt enforcement and polite_get behaviour.

All tests are pure unit tests — no real network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest

from housing_list_search.robots_cache import RobotsEntry, clear_robots_cache


# ---------------------------------------------------------------------------
# is_allowed_by_robots
# ---------------------------------------------------------------------------

class TestRobotsRespect:

    @pytest.fixture(autouse=True)
    def _allow_test_urls(self):
        clear_robots_cache()
        with patch("housing_list_search.scraper.validate_http_url", side_effect=lambda url, **_: url):
            yield
        clear_robots_cache()

    def _make_rp(self, allowed: bool):
        rp = MagicMock()
        rp.can_fetch.return_value = allowed
        return rp

    def _robots_resp(self, text: str = "User-agent: *\nDisallow:\n"):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.text = text
        mock_resp.iter_content.return_value = [text.encode()]
        return mock_resp

    def test_disallowed_url_returns_false(self):
        from housing_list_search.scraper import is_allowed_by_robots
        mock_rp = self._make_rp(False)
        entry = RobotsEntry(parser=mock_rp, treat_as_allowed=False)
        with patch("housing_list_search.scraper.get_robots_entry", return_value=entry):
            result = is_allowed_by_robots("https://example.gov/housing")
        assert result is False

    def test_allowed_url_returns_true(self):
        from housing_list_search.scraper import is_allowed_by_robots
        mock_rp = self._make_rp(True)
        entry = RobotsEntry(parser=mock_rp, treat_as_allowed=False)
        with patch("housing_list_search.scraper.get_robots_entry", return_value=entry):
            result = is_allowed_by_robots("https://example.gov/housing")
        assert result is True

    def test_unreachable_robots_treated_as_allowed(self):
        """Timeout / WAF block on robots.txt → treat as allowed (RFC-compliant)."""
        from housing_list_search.scraper import is_allowed_by_robots
        import requests as _req
        with patch(
            "housing_list_search.scraper.requests.get",
            side_effect=_req.exceptions.ConnectionError("connection refused"),
        ):
            result = is_allowed_by_robots("https://blocked.gov/housing")
        assert result is True

    def test_robots_url_constructed_from_origin(self):
        """robots.txt must be fetched from the scheme+host root, not a subpath."""
        from housing_list_search.scraper import is_allowed_by_robots
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"User-agent: *\nDisallow:\n"]
        mock_rp = self._make_rp(True)
        with (
            patch("housing_list_search.robots_cache.requests.get", return_value=mock_resp) as mock_get,
            patch("urllib.robotparser.RobotFileParser", return_value=mock_rp),
        ):
            is_allowed_by_robots("https://housing.sanjoseca.gov/listings")
        mock_get.assert_called_once()
        assert mock_get.call_args[0][0] == "https://housing.sanjoseca.gov/robots.txt"

    def test_robots_403_on_fetch_treated_as_allowed(self):
        """WAF 403 on robots.txt fetch must not set disallow_all and block the site."""
        from housing_list_search.scraper import is_allowed_by_robots
        entry = RobotsEntry(parser=None, treat_as_allowed=True)
        with patch("housing_list_search.scraper.get_robots_entry", return_value=entry):
            assert is_allowed_by_robots("https://jscosccha.com/") is True


# ---------------------------------------------------------------------------
# polite_get — robots.txt enforcement
# ---------------------------------------------------------------------------

class TestPoliteGet:

    @pytest.fixture(autouse=True)
    def _allow_test_urls(self):
        with patch("housing_list_search.scraper.validate_http_url", side_effect=lambda url, **_: url):
            yield

    def test_disallowed_url_never_fetched(self):
        """polite_get must not issue an HTTP request when robots.txt Disallows."""
        from housing_list_search.scraper import polite_get
        with (
            patch("housing_list_search.scraper.is_allowed_by_robots", return_value=False),
            patch("housing_list_search.scraper.requests.get") as mock_get,
        ):
            result = polite_get("https://disallowed.gov/page")
        assert result is None
        mock_get.assert_not_called()

    def test_allowed_url_is_fetched(self):
        from housing_list_search.scraper import polite_get
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"ok"]
        with (
            patch("housing_list_search.scraper.is_allowed_by_robots", return_value=True),
            patch("housing_list_search.scraper.requests.get", return_value=mock_resp),
            patch("housing_list_search.scraper.wait_for_host"),
            patch("housing_list_search.scraper.mark_host_fetched"),
        ):
            result = polite_get("https://allowed.gov/page")
        assert result is mock_resp
        assert result._content == b"ok"

    def test_404_returns_none(self):
        from housing_list_search.scraper import polite_get
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with (
            patch("housing_list_search.scraper.is_allowed_by_robots", return_value=True),
            patch("housing_list_search.scraper.requests.get", return_value=mock_resp),
            patch("housing_list_search.scraper.wait_for_host"),
            patch("housing_list_search.scraper.mark_host_fetched"),
        ):
            result = polite_get("https://example.gov/gone")
        assert result is None

    def test_403_returns_none(self):
        from housing_list_search.scraper import polite_get
        mock_resp = MagicMock()
        mock_resp.status_code = 403
        with (
            patch("housing_list_search.scraper.is_allowed_by_robots", return_value=True),
            patch("housing_list_search.scraper.requests.get", return_value=mock_resp),
            patch("housing_list_search.scraper.wait_for_host"),
            patch("housing_list_search.scraper.mark_host_fetched"),
        ):
            result = polite_get("https://example.gov/blocked")
        assert result is None

    def test_network_exception_returns_none(self):
        from housing_list_search.scraper import polite_get
        import requests as _req
        with (
            patch("housing_list_search.scraper.is_allowed_by_robots", return_value=True),
            patch("housing_list_search.scraper.requests.get", side_effect=_req.exceptions.ConnectionError("refused")),
            patch("housing_list_search.scraper.wait_for_host"),
            patch("housing_list_search.scraper.mark_host_fetched"),
        ):
            result = polite_get("https://example.gov/unreachable")
        assert result is None

    def test_private_ip_blocked_without_fetch(self):
        from housing_list_search.scraper import polite_get
        from housing_list_search.url_policy import validate_http_url as real_validate
        with (
            patch("housing_list_search.scraper.validate_http_url", real_validate),
            patch("housing_list_search.scraper.requests.get") as mock_get,
        ):
            result = polite_get("http://192.168.0.1/internal")
        assert result is None
        mock_get.assert_not_called()

    def test_oversized_response_returns_none(self):
        from housing_list_search.scraper import polite_get, DEFAULT_MAX_RESPONSE_BYTES
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"x" * (DEFAULT_MAX_RESPONSE_BYTES + 1)]
        with (
            patch("housing_list_search.scraper.is_allowed_by_robots", return_value=True),
            patch("housing_list_search.scraper.requests.get", return_value=mock_resp),
            patch("housing_list_search.scraper.wait_for_host"),
            patch("housing_list_search.scraper.mark_host_fetched"),
        ):
            result = polite_get("https://example.gov/huge")
        assert result is None
