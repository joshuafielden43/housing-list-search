"""
Unit tests for scraper.py robots.txt enforcement and polite_get behaviour.

All tests are pure unit tests — no real network calls.
"""

from __future__ import annotations

from unittest.mock import MagicMock, patch
import pytest


# ---------------------------------------------------------------------------
# is_allowed_by_robots
# ---------------------------------------------------------------------------

class TestRobotsRespect:

    def _make_rp(self, allowed: bool):
        rp = MagicMock()
        rp.can_fetch.return_value = allowed
        return rp

    def test_disallowed_url_returns_false(self):
        from housing_list_search.scraper import is_allowed_by_robots
        with patch("urllib.robotparser.RobotFileParser") as MockRP:
            MockRP.return_value = self._make_rp(False)
            result = is_allowed_by_robots("https://example.gov/housing")
        assert result is False

    def test_allowed_url_returns_true(self):
        from housing_list_search.scraper import is_allowed_by_robots
        with patch("urllib.robotparser.RobotFileParser") as MockRP:
            MockRP.return_value = self._make_rp(True)
            result = is_allowed_by_robots("https://example.gov/housing")
        assert result is True

    def test_unreachable_robots_treated_as_allowed(self):
        """Timeout / WAF block on robots.txt → treat as allowed (RFC-compliant)."""
        from housing_list_search.scraper import is_allowed_by_robots
        with patch("urllib.robotparser.RobotFileParser") as MockRP:
            rp = MagicMock()
            rp.read.side_effect = Exception("connection refused")
            MockRP.return_value = rp
            result = is_allowed_by_robots("https://blocked.gov/housing")
        assert result is True

    def test_robots_url_constructed_from_origin(self):
        """robots.txt must be fetched from the scheme+host root, not a subpath."""
        from housing_list_search.scraper import is_allowed_by_robots
        with patch("urllib.robotparser.RobotFileParser") as MockRP:
            rp = self._make_rp(True)
            MockRP.return_value = rp
            is_allowed_by_robots("https://housing.sanjoseca.gov/listings")
        rp.set_url.assert_called_once_with("https://housing.sanjoseca.gov/robots.txt")


# ---------------------------------------------------------------------------
# polite_get — robots.txt enforcement
# ---------------------------------------------------------------------------

class TestPoliteGet:

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
        mock_resp.content = b"ok"
        mock_resp.text = "ok"
        with (
            patch("housing_list_search.scraper.is_allowed_by_robots", return_value=True),
            patch("housing_list_search.scraper.requests.get", return_value=mock_resp),
            patch("housing_list_search.scraper.time.sleep"),
        ):
            result = polite_get("https://allowed.gov/page")
        assert result is mock_resp

    def test_404_returns_none(self):
        from housing_list_search.scraper import polite_get
        mock_resp = MagicMock()
        mock_resp.status_code = 404
        with (
            patch("housing_list_search.scraper.is_allowed_by_robots", return_value=True),
            patch("housing_list_search.scraper.requests.get", return_value=mock_resp),
            patch("housing_list_search.scraper.time.sleep"),
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
            patch("housing_list_search.scraper.time.sleep"),
        ):
            result = polite_get("https://example.gov/blocked")
        assert result is None

    def test_network_exception_returns_none(self):
        from housing_list_search.scraper import polite_get
        import requests as _req
        with (
            patch("housing_list_search.scraper.is_allowed_by_robots", return_value=True),
            patch("housing_list_search.scraper.requests.get", side_effect=_req.exceptions.ConnectionError("refused")),
            patch("housing_list_search.scraper.time.sleep"),
        ):
            result = polite_get("https://example.gov/unreachable")
        assert result is None
