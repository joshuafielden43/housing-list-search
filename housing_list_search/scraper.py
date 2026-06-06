# scraper.py
import requests
import time
import urllib.robotparser
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

USER_AGENT = "HousingListAggregator-Nonprofit-SantaClara-v1 (contact: joshua@fielden.org)"


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
        parsed = urlparse(url)
        base = f"{parsed.scheme}://{parsed.netloc}"
        robots_url = f"{base}/robots.txt"

        rp = urllib.robotparser.RobotFileParser()
        rp.set_url(robots_url)
        rp.read()

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

    except Exception as exc:
        # robots.txt unreachable (timeout, WAF block, DNS failure, etc.)
        # Treat as allowed per RFC; log at DEBUG so WAF investigation has a trace.
        logger.debug("robots.txt check for %s failed (%s) — treating as allowed", url, exc)
        return True


def polite_get(url: str, delay: int = 3):
    """
    Polite HTTP GET with robots.txt check and rate limiting.

    Returns the Response on success, None on any failure (404, 403, network
    error). All failures are logged as warnings with actionable guidance.

    robots.txt is checked first. If the URL is explicitly Disallowed, returns
    None immediately without making the request.
    """
    if not is_allowed_by_robots(url):
        return None

    headers = {"User-Agent": USER_AGENT}
    try:
        logger.debug("Fetching: %s", url)
        resp = requests.get(url, headers=headers, timeout=15)
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
        logger.debug("Success: %s (%d bytes)", url, len(resp.content))
        return resp

    except requests.exceptions.HTTPError as exc:
        logger.warning("HTTP error on %s: %s", url, exc)
        return None
    except Exception as exc:
        logger.warning("Request failed for %s: %s", url, exc)
        return None
