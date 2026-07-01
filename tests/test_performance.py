"""Tests for robots cache, host throttle, and parallel target scraping."""

from __future__ import annotations

import threading
import time
from unittest.mock import MagicMock, patch

import pytest

from housing_list_search.host_throttle import mark_host_fetched, reset_host_throttle, wait_for_host
from housing_list_search.pipeline import RunPipeline, max_target_workers
from housing_list_search.robots_cache import RobotsEntry, clear_robots_cache, get_robots_entry


@pytest.fixture(autouse=True)
def _reset_http_state():
    clear_robots_cache()
    reset_host_throttle()
    yield
    clear_robots_cache()
    reset_host_throttle()


class TestRobotsCache:
    def test_cache_hits_once_per_host(self):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.iter_content.return_value = [b"User-agent: *\nDisallow:\n"]

        with patch("housing_list_search.robots_cache.requests.get", return_value=mock_resp) as mock_get:
            get_robots_entry("https://example.gov", "https://example.gov/robots.txt")
            get_robots_entry("https://example.gov", "https://example.gov/robots.txt")

        assert mock_get.call_count == 1

    def test_is_allowed_uses_cache(self):
        from housing_list_search.scraper import is_allowed_by_robots

        mock_rp = MagicMock()
        mock_rp.can_fetch.return_value = True
        entry = RobotsEntry(parser=mock_rp, treat_as_allowed=False)

        with (
            patch("housing_list_search.scraper.validate_http_url", side_effect=lambda url, **_: url),
            patch("housing_list_search.scraper.get_robots_entry", return_value=entry) as mock_cache,
        ):
            assert is_allowed_by_robots("https://example.gov/page") is True
            assert is_allowed_by_robots("https://example.gov/other") is True

        assert mock_cache.call_count == 2


class TestHostThrottle:
    def test_serializes_requests_to_same_host(self):
        mark_host_fetched("https://example.gov/a")

        start = time.monotonic()
        wait_for_host("https://example.gov/b", delay=0.15)
        elapsed = time.monotonic() - start

        assert elapsed >= 0.12

    def test_different_hosts_do_not_block(self):
        results: list[float] = []

        def worker(host: str) -> None:
            wait_for_host(f"https://{host}/", delay=0.2)
            results.append(time.monotonic())

        t1 = threading.Thread(target=worker, args=("a.example.gov",))
        t2 = threading.Thread(target=worker, args=("b.example.gov",))
        start = time.monotonic()
        t1.start()
        t2.start()
        t1.join()
        t2.join()
        assert time.monotonic() - start < 0.18


class TestParallelTargets:
    def test_max_target_workers_env(self, monkeypatch):
        monkeypatch.setenv("HLS_MAX_TARGET_WORKERS", "5")
        assert max_target_workers() == 5
        monkeypatch.setenv("HLS_MAX_TARGET_WORKERS", "99")
        assert max_target_workers() == 8
        monkeypatch.setenv("HLS_MAX_TARGET_WORKERS", "0")
        assert max_target_workers() == 1

    def test_parallel_scrape_runs_all_targets(self):
        active = threading.Event()
        seen: list[str] = []
        lock = threading.Lock()

        def slow_scrape(target, failures=None):
            with lock:
                seen.append(target["authority"])
            active.wait(timeout=2)
            return [{"authority": target["authority"], "property_name": "P", "url": ""}]

        active.set()
        targets = [{"authority": f"City {i}", "url": f"https://{i}.example/"} for i in range(4)]

        with patch("housing_list_search.pipeline.max_target_workers", return_value=4):
            listings, failed = RunPipeline._scrape_targets(targets, slow_scrape)

        assert len(listings) == 4
        assert failed == []
        assert len(seen) == 4