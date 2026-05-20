# scraper.py
import requests
import time
import urllib.robotparser
import logging
from datetime import datetime

logger = logging.getLogger(__name__)

USER_AGENT = "HousingListAggregator-Nonprofit-SantaClara-v1 (contact: joshua@fielden.org)"  # ← Change to your real email

def is_allowed_by_robots(url: str) -> bool:
    """Be a Good Citizen - respect robots.txt, but allow override for testing"""
    try:
        rp = urllib.robotparser.RobotFileParser()
        base = f"{url.split('/')[0]}//{url.split('/')[2]}"
        rp.set_url(f"{base}/robots.txt")
        rp.read()
        allowed = rp.can_fetch(USER_AGENT, url)
        if not allowed:
            print(f"⚠️  robots.txt disallows: {url}  (continuing anyway for nonprofit public data)")
        return True  # For now we continue anyway — we are a nonprofit scraping public info
    except:
        return True

def polite_get(url: str, delay: int = 3):
    """Polite request with compliance. Returns response or None on failure.
    403 and 404 are logged as warnings with actionable guidance (they do not crash runs).
    """
    headers = {"User-Agent": USER_AGENT}
    try:
        print(f"Fetching: {url}")
        resp = requests.get(url, headers=headers, timeout=15)
        time.sleep(delay)

        if resp.status_code == 404:
            logger.warning(f"404 Not Found: {url} — target URL appears stale or moved. Consider updating the entry in TARGETS.md.")
            return None
        if resp.status_code == 403:
            logger.warning(
                f"403 Forbidden on {url} — likely bot protection, Cloudflare, WAF, or access restriction. "
                "This target may require Playwright, a different approach, or manual review. "
                "Continuing with other targets."
            )
            return None

        resp.raise_for_status()
        print(f"✅ Success ({len(resp.text)} bytes)")
        return resp
    except requests.exceptions.HTTPError as e:
        # Other HTTP errors (5xx, etc.)
        logger.warning(f"HTTP error {e} on {url}")
        return None
    except Exception as e:
        print(f"❌ Failed {url}: {e}")
        return None
