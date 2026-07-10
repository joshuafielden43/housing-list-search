# scraper.py
"""
HTTP implementation behind the Access seam (private to access.py).

Do not import this module from adapters, extraction, or pipeline.
Use ``housing_list_search.access`` instead (#1060).

Owns: URL policy / SSRF, robots, per-host throttle, bounded bodies, redirects,
nonprofit User-Agent, polite_get / polite_post.
"""

from __future__ import annotations

import ipaddress
import logging
import socket
import threading
import time
import urllib.robotparser
from dataclasses import dataclass
from urllib.parse import urljoin, urlparse

import requests

# Simple retry config for #985
_RETRYABLE_STATUS = (429, 500, 502, 503, 504)
_MAX_RETRIES = 3
_RETRY_BACKOFF = 1.5  # seconds base

# --- http limits (inlined) ---
USER_AGENT = "HousingListAggregator-Nonprofit-Santa Clara-v1 (contact: joshua@fielden.org)"
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # 20 MiB


def read_bounded_content(resp: requests.Response, max_bytes: int) -> bytes | None:
    """Read response body up to max_bytes. Returns None if the cap is exceeded."""
    chunks: list[bytes] = []
    total = 0
    for chunk in resp.iter_content(chunk_size=65536):
        if not chunk:
            continue
        total += len(chunk)
        if total > max_bytes:
            resp.close()
            return None
        chunks.append(chunk)
    return b"".join(chunks)


_read_bounded_content = read_bounded_content  # for internal polite_get


# --- url policy (inlined) ---
ALLOWED_SCHEMES = ("http", "https")

_BLOCKED_HOSTNAMES = frozenset(
    {
        "localhost",
        "metadata.google.internal",
        "metadata.goog",
    }
)

_BLOCKED_HOST_SUBSTRINGS = (
    ".local",
    ".internal",
    "169.254.",
)


class URLPolicyError(ValueError):
    """Raised when a URL fails outbound policy checks."""


class SourceFetchError(RuntimeError):
    """Inventory source fetch failed (network, HTTP error, policy, robots).

    Distinct from a successful response that happens to contain zero listings.
    Adapters raise this when polite_get/polite_post returns None for a required
    inventory URL so dispatch can set had_error and label SCRAPE_FAILED.

    ``partial`` may carry records already scraped (e.g. page 1 OK, page 2 failed)
    so the pipeline can upsert what it has while still marking the authority failed.
    """

    def __init__(self, message: str, *, partial: list | None = None):
        super().__init__(message)
        self.partial: list = list(partial or [])

    @classmethod
    def pagination_cap(
        cls,
        adapter: str,
        *,
        max_pages: int,
        partial: list | None = None,
        detail: str = "",
    ) -> SourceFetchError:
        """Build error when a safety page cap is hit with a full final page (#776).

        Inventory may be truncated — callers must not treat this as a clean success
        (dispatch → had_error → SCRAPE_FAILED). Partial rows are still upsertable.
        """
        extra = f" ({detail})" if detail else ""
        return cls(
            f"{adapter}: pagination hit max_pages={max_pages} with a full final page"
            f"{extra} — inventory may be truncated; mark SCRAPE_FAILED",
            partial=list(partial or []),
        )


def require_response(resp, url: str, *, context: str = ""):
    """Return resp or raise SourceFetchError when polite_get/post returned None."""
    if resp is not None:
        return resp
    where = f"{context}: " if context else ""
    raise SourceFetchError(f"{where}fetch failed for {url}")


def _hostname_is_blocked(hostname: str) -> bool:
    host = hostname.lower().rstrip(".")
    if not host:
        return True
    if host in _BLOCKED_HOSTNAMES:
        return True
    if any(part in host for part in _BLOCKED_HOST_SUBSTRINGS):
        return True
    return False


def _ip_is_public(ip: ipaddress.IPv4Address | ipaddress.IPv6Address) -> bool:
    return not (
        ip.is_private
        or ip.is_loopback
        or ip.is_link_local
        or ip.is_multicast
        or ip.is_reserved
        or ip.is_unspecified
    )


def _check_resolved_addresses(hostname: str) -> None:
    """Resolve hostname and reject if any address is non-public."""
    try:
        infos = socket.getaddrinfo(hostname, None, type=socket.SOCK_STREAM)
    except socket.gaierror as exc:
        raise URLPolicyError(f"DNS resolution failed for {hostname!r}: {exc}") from exc

    if not infos:
        raise URLPolicyError(f"DNS resolution returned no addresses for {hostname!r}")

    for info in infos:
        addr = info[4][0]
        try:
            ip = ipaddress.ip_address(addr)
        except ValueError:
            continue
        if not _ip_is_public(ip):
            raise URLPolicyError(f"URL host {hostname!r} resolves to non-public address {addr}")


def validate_http_url(url: str, *, resolve_dns: bool = True) -> str:
    """
    Validate that url is safe to fetch over HTTP(S).

    Returns the original url string on success.
    Raises URLPolicyError on policy violation.
    """
    if not url or not str(url).strip():
        raise URLPolicyError("URL is empty")

    url = str(url).strip()
    parsed = urlparse(url)

    if parsed.scheme not in ALLOWED_SCHEMES:
        raise URLPolicyError(f"Disallowed URL scheme: {parsed.scheme!r}")

    if parsed.username or parsed.password:
        raise URLPolicyError("URLs with embedded credentials are not allowed")

    hostname = parsed.hostname
    if not hostname:
        raise URLPolicyError("URL has no hostname")

    if _hostname_is_blocked(hostname):
        raise URLPolicyError(f"Blocked hostname: {hostname!r}")

    # Literal IP in the URL
    try:
        ip = ipaddress.ip_address(hostname)
    except ValueError:
        ip = None

    if ip is not None:
        if not _ip_is_public(ip):
            raise URLPolicyError(f"Non-public IP address in URL: {hostname}")
    elif resolve_dns:
        _check_resolved_addresses(hostname)

    return url


def is_safe_http_url(url: str, *, resolve_dns: bool = True) -> bool:
    """Non-raising policy check for sanitizers."""
    try:
        validate_http_url(url, resolve_dns=resolve_dns)
        return True
    except URLPolicyError as exc:
        logger.debug("URL policy rejected %r: %s", url, exc)
        return False


# --- host throttle (inlined) ---
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
    """Block until at least ``delay`` seconds have elapsed since the last fetch to this host.

    Claims the host slot before releasing the lock (#1053) so concurrent workers
    cannot both observe remaining==0 and start the same-host request together.
    ``mark_host_fetched`` still runs after the request to extend the gap for
    long-running responses.
    """
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
        # Reserve: next waiter measures from this claim, not from request end
        _HOST_LAST_FETCH[netloc] = time.monotonic()


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


# --- robots cache (inlined) ---
_ROBOTS_CACHE: dict[str, RobotsEntry] = {}
_CACHE_LOCK = threading.Lock()


@dataclass
class RobotsEntry:
    """Cached robots.txt evaluation state for one origin."""

    parser: urllib.robotparser.RobotFileParser | None
    treat_as_allowed: bool


def clear_robots_cache() -> None:
    """Reset cache (tests)."""
    with _CACHE_LOCK:
        _ROBOTS_CACHE.clear()


def get_robots_entry(base: str, robots_url: str) -> RobotsEntry:
    """Return cached robots entry for ``base`` (scheme://host), fetching on miss."""
    with _CACHE_LOCK:
        cached = _ROBOTS_CACHE.get(base)
        if cached is not None:
            return cached

    entry = _fetch_robots_entry(robots_url)

    with _CACHE_LOCK:
        _ROBOTS_CACHE[base] = entry
        return entry


def _fetch_robots_entry(robots_url: str) -> RobotsEntry:
    try:
        resp = requests.get(
            robots_url,
            headers={"User-Agent": USER_AGENT},
            timeout=15,
            stream=True,
        )
        content = _read_bounded_content(resp, 256 * 1024)
        if content is None:
            logger.warning(
                "robots.txt response exceeded size cap for %s — treating as unreachable (allowed)",
                robots_url,
            )
            return RobotsEntry(parser=None, treat_as_allowed=True)

        if resp.status_code in (401, 403):
            logger.warning(
                "robots.txt returned HTTP %d for %s — treating as unreachable (allowed). "
                "If this is a WAF block on the robots fetch itself, document in TARGETS.md.",
                resp.status_code,
                robots_url,
            )
            return RobotsEntry(parser=None, treat_as_allowed=True)
        if resp.status_code >= 400:
            return RobotsEntry(parser=None, treat_as_allowed=True)

        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.parse(content.decode("utf-8", errors="replace").splitlines())
        return RobotsEntry(parser=rp, treat_as_allowed=False)
    except Exception as exc:
        logger.debug("Failed to fetch robots.txt for %s: %s", robots_url, exc)
        return RobotsEntry(parser=None, treat_as_allowed=True)


logger = logging.getLogger(__name__)

_MAX_REDIRECT_HOPS = 10
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})
# Drop these on cross-origin redirects so bearer/session credentials cannot leak.
_SENSITIVE_HEADER_NAMES = frozenset({"authorization", "cookie", "proxy-authorization"})


def _url_origin(url: str) -> tuple[str, str, int | None]:
    """Return (scheme, hostname_lower, port) for same-origin comparison."""
    parsed = urlparse(url)
    host = (parsed.hostname or "").lower()
    port = parsed.port
    if port is None:
        if parsed.scheme == "https":
            port = 443
        elif parsed.scheme == "http":
            port = 80
    return (parsed.scheme.lower(), host, port)


def _strip_sensitive_headers(headers: dict[str, str]) -> dict[str, str]:
    """Copy headers without Authorization / Cookie (case-insensitive keys)."""
    return {k: v for k, v in headers.items() if k.lower() not in _SENSITIVE_HEADER_NAMES}


def _request_with_redirect_policy(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout: int,
    stream: bool = True,
    json: dict | list | None = None,
):
    """Issue HTTP request(s), validating each redirect target through url_policy.

    Cross-origin redirects (different scheme/host/port) strip Authorization and
    Cookie so authenticated callers (e.g. Vikunja Bearer) cannot leak to a
    third-party Location. Same-origin redirects keep credentials.
    """
    current = url
    active_headers = dict(headers)
    for _ in range(_MAX_REDIRECT_HOPS + 1):
        try:
            validate_http_url(current)
        except URLPolicyError as exc:
            logger.warning("URL policy blocked redirect target %s: %s", current, exc)
            return None

        kwargs: dict = {
            "headers": active_headers,
            "timeout": timeout,
            "stream": stream,
            "allow_redirects": False,
        }
        if json is not None:
            kwargs["json"] = json

        if method == "POST":
            resp = requests.post(current, **kwargs)
        else:
            resp = requests.get(current, **kwargs)

        if resp.status_code not in _REDIRECT_STATUS:
            return resp

        location = resp.headers.get("Location") or resp.headers.get("location")
        resp.close()
        if not location:
            logger.warning("Redirect from %s missing Location header", current)
            return None
        next_url = urljoin(current, location)
        if _url_origin(next_url) != _url_origin(current):
            stripped = _strip_sensitive_headers(active_headers)
            if stripped != active_headers:
                logger.info(
                    "Stripped sensitive headers on cross-origin redirect %s → %s",
                    current,
                    next_url,
                )
            active_headers = stripped
        current = next_url

    logger.warning("Redirect hop limit exceeded for %s", url)
    return None


def is_allowed_by_robots(url: str) -> bool:
    """
    Check robots.txt for the given URL.

    Returns True if the URL is allowed (or if robots.txt is unreachable).
    Returns False only when robots.txt is reachable AND explicitly Disallows
    our User-Agent or *.

    WAF-blocked sites (Akamai, etc.) return a non-200 or a timeout when
    fetching robots.txt from automation. Those are treated as "unknown" →
    True (allowed), consistent with the Robots Exclusion Protocol which
    says robots.txt absence means no restrictions. We log a debug note so
    the WAF status is visible.

    Note on browser User-Agent spoofing: the Bloom Housing API path uses
    a Chrome User-Agent header to avoid bot-detection on the REST endpoint.
    That does not affect robots.txt evaluation — we always check robots.txt
    under our own nonprofit User-Agent string.
    """
    try:
        validate_http_url(url)
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = f"{base}/robots.txt"
        validate_http_url(robots_url)

        entry = get_robots_entry(base, robots_url)
        if entry.treat_as_allowed or entry.parser is None:
            return True

        allowed = entry.parser.can_fetch(USER_AGENT, url)
        if not allowed:
            logger.warning(
                "robots.txt Disallows %s for %s — skipping this URL. "
                "If this is wrong (e.g. WAF returned a fake Disallow page), "
                "document it in TARGETS.md and handle at the call site.",
                url,
                USER_AGENT,
            )
            return False
        return True

    except URLPolicyError as exc:
        logger.warning("URL policy blocked robots check for %s: %s", url, exc)
        return False
    except Exception as exc:
        # robots.txt unreachable (timeout, WAF block, DNS failure, etc.)
        # Treat as allowed per RFC; log at DEBUG so WAF investigation has a trace.
        logger.debug("robots.txt check for %s failed (%s) — treating as allowed", url, exc)
        return True


def polite_get(
    url: str,
    delay: int = 3,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
    *,
    headers: dict[str, str] | None = None,
):
    """
    Polite HTTP GET with URL policy, robots.txt check, rate limiting, and
    bounded response size.

    Optional ``headers`` are merged over the nonprofit User-Agent (caller wins
    on key collision except User-Agent is always set first then updated). Use
    for authenticated APIs (e.g. Vikunja Bearer). Cross-origin redirects strip
    Authorization/Cookie — see ``_request_with_redirect_policy``.

    Returns the Response on success, None on any failure (404, 403, network
    error, policy violation, oversize body). All failures are logged as
    warnings with actionable guidance.

    robots.txt is checked first. If the URL is explicitly Disallowed, returns
    None immediately without making the request.
    """
    try:
        validate_http_url(url)
    except URLPolicyError as exc:
        logger.warning("URL policy blocked fetch for %s: %s", url, exc)
        return None

    if not is_allowed_by_robots(url):
        return None

    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)
    try:
        wait_for_host(url, delay)
        logger.debug("Fetching: %s", url)
        resp = None
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = _request_with_redirect_policy(
                    "GET", url, headers=req_headers, timeout=15, stream=True
                )
            except URLPolicyError as exc:
                # Policy blocks are final, no retry
                logger.warning("URL policy blocked fetch for %s: %s", url, exc)
                return None
            except Exception as exc:  # pragma: no cover
                last_exc = exc
                resp = None
            if resp is not None and resp.status_code not in _RETRYABLE_STATUS:
                break
            # Only retry when we actually got a retryable *response* status (policy None or other None = final)
            if attempt < _MAX_RETRIES - 1 and resp is not None and resp.status_code in _RETRYABLE_STATUS:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            break
        if resp is None:
            if last_exc:
                logger.warning("Request failed after retries for %s: %s", url, last_exc)
            return None

        if resp.status_code == 404:
            logger.warning(
                "404 Not Found: %s — target URL appears stale or moved. "
                "Consider updating TARGETS.md.",
                url,
            )
            return None
        if resp.status_code == 403:
            logger.warning(
                "403 Forbidden: %s — likely WAF/bot-protection. "
                "This target may need Playwright or manual review.",
                url,
            )
            return None

        resp.raise_for_status()
        content = _read_bounded_content(resp, max_bytes)
        if content is None:
            logger.warning(
                "Response body exceeded %d-byte cap for %s — discarding",
                max_bytes,
                url,
            )
            return None

        resp._content = content
        logger.debug("Success: %s (%d bytes)", url, len(content))
        return resp

    except requests.exceptions.HTTPError as exc:
        logger.warning("HTTP error on %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("Request failed for %s: %s", url, exc)
        return None
    finally:
        mark_host_fetched(url)


def polite_post(
    url: str,
    *,
    json: dict | list | None = None,
    headers: dict[str, str] | None = None,
    delay: int = 3,
    max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES,
):
    """
    Polite HTTP POST with URL policy, robots.txt check, rate limiting, and
    bounded response size.

    Bloom REST and similar JSON APIs use POST; this mirrors polite_get policy
    without treating POST as exempt from outbound trust checks.
    """
    try:
        validate_http_url(url)
    except URLPolicyError as exc:
        logger.warning("URL policy blocked POST for %s: %s", url, exc)
        return None

    if not is_allowed_by_robots(url):
        return None

    req_headers = {"User-Agent": USER_AGENT}
    if headers:
        req_headers.update(headers)

    try:
        wait_for_host(url, delay)
        logger.debug("POST: %s", url)
        resp = None
        last_exc = None
        for attempt in range(_MAX_RETRIES):
            try:
                resp = _request_with_redirect_policy(
                    "POST",
                    url,
                    headers=req_headers,
                    timeout=30,
                    stream=True,
                    json=json,
                )
            except URLPolicyError as exc:
                logger.warning("URL policy blocked POST for %s: %s", url, exc)
                return None
            except Exception as exc:
                last_exc = exc
                resp = None
            if resp is not None and resp.status_code not in _RETRYABLE_STATUS:
                break
            if attempt < _MAX_RETRIES - 1 and resp is not None and resp.status_code in _RETRYABLE_STATUS:
                time.sleep(_RETRY_BACKOFF * (attempt + 1))
                continue
            break
        if resp is None:
            if last_exc:
                logger.warning("POST failed after retries for %s: %s", url, last_exc)
            return None

        if resp.status_code == 404:
            logger.warning(
                "404 Not Found on POST %s — endpoint may have moved. Update adapter config.",
                url,
            )
            return None
        if resp.status_code == 403:
            logger.warning(
                "403 Forbidden on POST %s — likely WAF/bot-protection.",
                url,
            )
            return None

        resp.raise_for_status()
        content = _read_bounded_content(resp, max_bytes)
        if content is None:
            logger.warning(
                "POST response body exceeded %d-byte cap for %s — discarding",
                max_bytes,
                url,
            )
            return None

        resp._content = content
        logger.debug("POST success: %s (%d bytes)", url, len(content))
        return resp

    except requests.exceptions.HTTPError as exc:
        logger.warning("HTTP error on POST %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("POST failed for %s: %s", url, exc)
        return None
    finally:
        mark_host_fetched(url)
