"""
Per-host rate limiting for polite_get and parallel target runs.

When RunPipeline scrapes targets concurrently, requests to the same origin
must still respect the configured delay between fetches.
"""

from __future__ import annotations

import threading
import time
from urllib.parse import urlparse

_HOST_LOCKS: dict[str, threading.Lock] = {}
_HOST_LAST_FETCH: dict[str, float] = {}
_META_LOCK = threading.Lock()


def _netloc(url_or_host: str) -> str:
    if "://" in url_or_host:
        return urlparse(url_or_host).netloc or ""
    return url_or_host


def _host_lock(netloc: str) -> threading.Lock:
    with _META_LOCK:
        lock = _HOST_LOCKS.get(netloc)
        if lock is None:
            lock = threading.Lock()
            _HOST_LOCKS[netloc] = lock
        return lock


def wait_for_host(url_or_host: str, delay: float) -> None:
    """Block until at least ``delay`` seconds have elapsed since the last fetch to this host."""
    netloc = _netloc(url_or_host)
    if not netloc or delay <= 0:
        return

    lock = _host_lock(netloc)
    with lock:
        now = time.monotonic()
        last = _HOST_LAST_FETCH.get(netloc, 0.0)
        remaining = delay - (now - last)
        if remaining > 0:
            time.sleep(remaining)


def mark_host_fetched(url_or_host: str) -> None:
    """Record that a fetch to this host completed (success or failure)."""
    netloc = _netloc(url_or_host)
    if not netloc:
        return
    lock = _host_lock(netloc)
    with lock:
        _HOST_LAST_FETCH[netloc] = time.monotonic()


def reset_host_throttle() -> None:
    """Clear throttle state (tests)."""
    with _META_LOCK:
        _HOST_LAST_FETCH.clear()