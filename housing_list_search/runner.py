"""
runner.py — measure-driven target dispatcher

run_target(target_row) is the single function responsible for deciding which
adapter(s) to invoke for one TARGETS.md row. It returns a list of plain dicts
(already coerced via to_dict() where needed).

Design rules:
- Dispatch is driven entirely by scraping_measures — never by URL substrings or
  authority name patterns. Add a measure to TARGETS.md; the code follows.
- Every named measure maps to exactly one adapter call. Unknown measures are
  logged and skipped, never silently routed to generic scraping.
- Multi-measure targets (e.g. housekeys,civicplus) run every matching adapter.
  A measure that produces zero records does not suppress other measures.
- Playwright and generic-scrape are fallbacks of last resort, activated only
  when no named measure fired AND the target is not waf_blocked.
- waf_blocked rows are skipped immediately with a single WARNING log line.
  They never consume network time.
"""

from __future__ import annotations

import logging
from typing import Any

logger = logging.getLogger(__name__)

# Module-level adapter imports — kept at top so tests can patch them cleanly.
# Each import is guarded so runner.py can be imported in environments that are
# missing optional dependencies (e.g. CI without playwright).
try:
    from housing_list_search.extraction import extract_target
except Exception:
    extract_target = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.john_stewart import scrape_john_stewart
except Exception:
    scrape_john_stewart = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.gis_extraction import extract_gis_portfolio
except Exception:
    extract_gis_portfolio = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.housekeys import scrape_housekeys
except Exception:
    scrape_housekeys = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.civicplus import extract_underlying_records
except Exception:
    extract_underlying_records = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.alta import scrape_alta
except Exception:
    scrape_alta = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.charities_housing import scrape_charities_housing
except Exception:
    scrape_charities_housing = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.midpen import scrape_midpen
except Exception:
    scrape_midpen = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.eden import scrape_eden
except Exception:
    scrape_eden = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.eah import scrape_eah
except Exception:
    scrape_eah = None  # type: ignore[assignment]

try:
    from housing_list_search.adapters.first_housing import scrape_first_housing
except Exception:
    scrape_first_housing = None  # type: ignore[assignment]

try:
    from housing_list_search.playwright_scraper import playwright_scrape
except Exception:
    playwright_scrape = None  # type: ignore[assignment]

try:
    from housing_list_search.scraper import polite_get
    from housing_list_search.generic_scraper import generic_scrape
except Exception:
    polite_get = None  # type: ignore[assignment]
    generic_scrape = None  # type: ignore[assignment]


def run_target(target: dict[str, Any], *, failures: list[str] | None = None) -> list[dict]:
    """
    Dispatch one TARGETS.md row to the appropriate adapter(s).

    target: a dict with keys authority, url, scraping_measures,
            administrator, administrator_url, administrator_phone,
            administrator_contact, notes.

    Returns a list of plain dicts ready for dedupe + normalizer.
    """
    authority = target.get("authority", "")
    url = target.get("url", "")
    measures_raw = target.get("scraping_measures") or ""
    measures = {m.strip() for m in measures_raw.split(",") if m.strip()}
    admin_url = target.get("administrator_url") or ""
    admin = target.get("administrator") or ""
    admin_phone = target.get("administrator_phone") or ""
    admin_contact = target.get("administrator_contact") or ""

    # ----------------------------------------------------------------
    # 0. Hard skip — WAF-blocked targets waste 30+ seconds and return nothing
    # ----------------------------------------------------------------
    if "waf_blocked" in measures:
        logger.warning(
            "SKIPPING %s — waf_blocked (Akamai IP-range block; "
            "robots.txt unreachable; manual browser inspection required). "
            "See TARGETS.md notes.",
            authority,
        )
        return []

    # ----------------------------------------------------------------
    # 1. Extraction layer (Bloom Housing, etc.)
    #    Contributes to results but does NOT short-circuit named-measure
    #    adapters. A row with both a Bloom URL and housekeys,civicplus measures
    #    (e.g. a future city using both) will run all three sources.
    # ----------------------------------------------------------------
    results: list[dict] = []
    ran_any = False
    had_error = False

    def _note_error() -> None:
        nonlocal had_error
        had_error = True

    if extract_target is not None:
        try:
            ext_records = extract_target(url, authority)
            if ext_records:
                logger.info("[runner] %s: %d records via extraction layer", authority, len(ext_records))
                results.extend(r.to_dict() if hasattr(r, "to_dict") else r for r in ext_records)
                ran_any = True
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: extraction layer failed (%s) — falling through to adapters", authority, exc)

    # ----------------------------------------------------------------
    # 2. Named-measure adapters — run every matching measure
    # ----------------------------------------------------------------

    if "john_stewart" in measures and scrape_john_stewart is not None:
        try:
            recs = scrape_john_stewart(url)
            results.extend(recs)
            ran_any = True
            logger.info("[runner] %s: john_stewart → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: john_stewart failed: %s", authority, exc)

    if "gis" in measures and extract_gis_portfolio is not None:
        try:
            recs = extract_gis_portfolio(
                url, authority,
                administrator=admin,
                administrator_url=admin_url,
                administrator_phone=admin_phone,
                administrator_contact=admin_contact,
            )
            results.extend(recs)
            # A named adapter that ran cleanly counts as fired even with zero
            # records — generic fallback on these pages scrapes prose as
            # listings, which is worse than an honest empty result.
            ran_any = True
            logger.info("[runner] %s: gis → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: gis failed: %s", authority, exc)

    if "housekeys" in measures and scrape_housekeys is not None:
        try:
            recs = scrape_housekeys(authority, url, admin_url=admin_url)
            results.extend(recs)
            ran_any = True
            logger.info("[runner] %s: housekeys → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: housekeys failed: %s", authority, exc)

    # "cdn" is the legacy name for the civicplus measure — accepted for old TARGETS.md rows
    if measures & {"civicplus", "cdn"} and extract_underlying_records is not None:
        try:
            recs = extract_underlying_records(url, authority)
            results.extend(recs)
            ran_any = True  # ran cleanly — zero records must not trigger generic fallback
            logger.info("[runner] %s: civicplus → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: civicplus failed: %s", authority, exc)

    if "first_housing" in measures and scrape_first_housing is not None:
        try:
            recs = scrape_first_housing(authority, url)
            results.extend(recs)
            ran_any = True
            logger.info("[runner] %s: first_housing → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: first_housing failed: %s", authority, exc)

    if "eden" in measures and scrape_eden is not None:
        try:
            recs = scrape_eden(authority, url)
            results.extend(recs)
            ran_any = True
            logger.info("[runner] %s: eden → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: eden failed: %s", authority, exc)

    if "eah" in measures and scrape_eah is not None:
        try:
            recs = scrape_eah(authority, url)
            results.extend(recs)
            ran_any = True
            logger.info("[runner] %s: eah → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: eah failed: %s", authority, exc)

    if "midpen" in measures and scrape_midpen is not None:
        try:
            recs = scrape_midpen(authority, url)
            results.extend(recs)
            ran_any = True
            logger.info("[runner] %s: midpen → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: midpen failed: %s", authority, exc)

    if "charities_housing" in measures and scrape_charities_housing is not None:
        try:
            recs = scrape_charities_housing(authority, url)
            results.extend(recs)
            ran_any = True
            logger.info("[runner] %s: charities_housing → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: charities_housing failed: %s", authority, exc)

    if "alta" in measures and scrape_alta is not None:
        try:
            recs = scrape_alta(authority, url)
            results.extend(recs)
            ran_any = True
            logger.info("[runner] %s: alta → %d records", authority, len(recs))
        except Exception as exc:
            _note_error()
            logger.warning("[runner] %s: alta failed: %s", authority, exc)

    # Log any measures we don't recognise so TARGETS.md typos surface immediately
    known = {
        "john_stewart", "gis", "housekeys", "civicplus", "cdn", "alta",
        "charities_housing", "midpen", "eden", "eah", "first_housing",
        "waf_blocked", "no_public_list",
        # Informational / routing hints — not adapter triggers
        "native_requests", "js_heavy", "table_based", "html_cards",
        "playwright_needed", "robots_respect", "delegated_administrator",
        "notification_based", "monitor_housing_element",
    }
    unknown = measures - known
    if unknown:
        logger.warning("[runner] %s: unrecognised measures %s — check TARGETS.md", authority, unknown)

    # ----------------------------------------------------------------
    # 3. Last-resort fallbacks — only when no named adapter fired
    # ----------------------------------------------------------------
    if not ran_any:
        if "playwright_needed" in measures or "js_heavy" in measures:
            if playwright_scrape is not None:
                try:
                    recs = playwright_scrape(authority, url)
                    results.extend(recs)
                    logger.info("[runner] %s: playwright fallback → %d records", authority, len(recs))
                except Exception as exc:
                    _note_error()
                    logger.warning("[runner] %s: playwright failed: %s", authority, exc)
        elif polite_get is not None and generic_scrape is not None:
            try:
                resp = polite_get(url)
                if resp:
                    recs = generic_scrape(authority, url, resp.text)
                    results.extend(recs)
                    logger.info("[runner] %s: generic fallback → %d records", authority, len(recs))
            except Exception as exc:
                _note_error()
                logger.warning("[runner] %s: generic fallback failed: %s", authority, exc)

    if had_error and failures is not None and authority not in failures:
        failures.append(authority)

    return results
