# scraper.py
import requests
import time
import urllib.robotparser
import logging
from datetime import datetime

from housing_list_search.url_policy import URLPolicyError, validate_http_url

logger = logging.getLogger(__name__)

USER_AGENT = "HousingListAggregator-Nonprofit-SantaClara-v1 (contact: joshua@fielden.org)"

# Cap response bodies to limit memory exhaustion and unbounded downloads.
DEFAULT_MAX_RESPONSE_BYTES = 20 * 1024 * 1024  # 20 MiB


def _read_bounded_content(resp: requests.Response, max_bytes: int) -> bytes | None:
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
        from urllib.parse import urlparse
        validate_http_url(url)
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = f"{base}/robots.txt"
        validate_http_url(robots_url)

        # Fetch with our nonprofit UA — NOT RobotFileParser.read(), which uses
        # Python-urllib's default bot string. Cloudflare (jscosccha.com, etc.)
        # returns 403 for that default UA; urllib.robotparser then sets
        # disallow_all=True and blocks every URL even when robots.txt allows /.
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
            return True

        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        if resp.status_code in (401, 403):
            logger.warning(
                "robots.txt returned HTTP %d for %s — treating as unreachable (allowed). "
                "If this is a WAF block on the robots fetch itself, document in TARGETS.md.",
                resp.status_code, robots_url,
            )
            return True
        if resp.status_code >= 400:
            return True
        rp.parse(content.decode("utf-8", errors="replace").splitlines())

        allowed = rp.can_fetch(USER_AGENT, url)
        if not allowed:
            logger.warning(
                "robots.txt Disallows %s for %s — skipping this URL. "
                "If this is wrong (e.g. WAF returned a fake Disallow page), "
                "document it in TARGETS.md and handle at the call site.",
                url, USER_AGENT,
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
        logger.debug("Fetching: %s", url)
        resp = requests.get(url, headers=headers, timeout=15, stream=True)
        time.sleep(delay)

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