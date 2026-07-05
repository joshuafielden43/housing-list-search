# scraper.py
import logging
from urllib.parse import urljoin, urlparse

import requests

from housing_list_search.host_throttle import mark_host_fetched, wait_for_host
from housing_list_search.http_limits import (
    DEFAULT_MAX_RESPONSE_BYTES,
    USER_AGENT,
)
from housing_list_search.http_limits import (
    read_bounded_content as _read_bounded_content,
)
from housing_list_search.robots_cache import get_robots_entry
from housing_list_search.url_policy import URLPolicyError, validate_http_url

logger = logging.getLogger(__name__)

_MAX_REDIRECT_HOPS = 10
_REDIRECT_STATUS = frozenset({301, 302, 303, 307, 308})


def _request_with_redirect_policy(
    method: str,
    url: str,
    *,
    headers: dict[str, str],
    timeout: int,
    stream: bool = True,
    json: dict | list | None = None,
):
    """Issue HTTP request(s), validating each redirect target through url_policy."""
    current = url
    for _ in range(_MAX_REDIRECT_HOPS + 1):
        try:
            validate_http_url(current)
        except URLPolicyError as exc:
            logger.warning("URL policy blocked redirect target %s: %s", current, exc)
            return None

        kwargs: dict = {
            "headers": headers,
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
        current = urljoin(current, location)

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


def polite_get(url: str, delay: int = 3, max_bytes: int = DEFAULT_MAX_RESPONSE_BYTES):
    """
    Polite HTTP GET with URL policy, robots.txt check, rate limiting, and
    bounded response size.

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

    headers = {"User-Agent": USER_AGENT}
    try:
        wait_for_host(url, delay)
        logger.debug("Fetching: %s", url)
        resp = _request_with_redirect_policy("GET", url, headers=headers, timeout=15, stream=True)
        if resp is None:
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
        resp = _request_with_redirect_policy(
            "POST",
            url,
            headers=req_headers,
            timeout=30,
            stream=True,
            json=json,
        )
        if resp is None:
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
