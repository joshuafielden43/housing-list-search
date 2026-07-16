"""
dispatch.py — unified Target dispatch registry.

The core of the (now collapsed) Target Scrape seam.
scrape_target() is the primary entry point; returns TargetScrapeResult.
Measures map to adapter handlers; URL predicates map to extraction-layer handlers.
"""

from __future__ import annotations

import importlib
import logging
from collections.abc import Callable
from dataclasses import dataclass
from typing import Any

from housing_list_search.listing import coerce_adapter_records
from housing_list_search.measure_registry import (
    KNOWN_MEASURES,
    MEASURE_ALIASES,
    parse_target_measures,
)
from housing_list_search.target_context import TargetContext

# TargetContext is defined in target_context.py; re-exported for dispatch callers.


@dataclass(frozen=True)
class TargetScrapeResult:
    """Explicit result of scraping one target.

    Deepened seam artifact. Carries authority for self-description, raw records
    (pre-Listing canonical for consumers like suspicious_zero), and had_error.
    Replaces implicit "phantom" tracking via mutable failures lists.
    had_error=True means some part of the scrape raised; records may be partial.
    The caller decides what that implies for SCRAPE_FAILED vs STALE.
    """

    authority: str
    records: list[Record]
    had_error: bool = False


logger = logging.getLogger(__name__)

Record = dict[str, Any]
Handler = Callable[[TargetContext], list[Record]]
UrlPredicate = Callable[[str, str], bool]
UrlExtractor = Callable[[str, str], list[Any]]

_MEASURE_HANDLERS: dict[str, Handler] = {}
_URL_EXTRACTORS: list[tuple[str, frozenset[str], UrlPredicate, UrlExtractor]] = []


def register_measure(measure: str, handler: Handler) -> None:
    _MEASURE_HANDLERS[measure] = handler


def registered_handler_measures() -> frozenset[str]:
    """Measures with a registered adapter handler (for drift checks vs measure_registry)."""
    return frozenset(_MEASURE_HANDLERS)


def register_url_extractor(
    label: str,
    predicate: UrlPredicate,
    extractor: UrlExtractor,
    *,
    measures: frozenset[str] | None = None,
) -> None:
    """Register a URL-driven extractor. measures=None means predicate alone gates."""
    required = measures if measures is not None else frozenset()
    _URL_EXTRACTORS.append((label, required, predicate, extractor))


def _coerce_records(raw: list[Any]) -> list[Record]:
    """Delegate to listing.coerce_adapter_records — single boundary (#801)."""
    return coerce_adapter_records(raw)


def extract_target(url: str, authority: str = "") -> list[Any]:
    """
    Standalone URL extraction (integration tests, ground_truth).
    Predicate-gated only — no measure required.
    """
    ensure_registered()
    return _run_url_extractors(url, authority, set(), ignore_measure_gate=True)


def _run_url_extractors(
    url: str,
    authority: str,
    measures: set[str],
    *,
    url_extractor: UrlExtractor | None = None,
    ignore_measure_gate: bool = False,
) -> list[Record]:
    if url_extractor is not None:
        return _coerce_records(url_extractor(url, authority))

    results: list[Record] = []
    for label, required_measures, predicate, extractor in _URL_EXTRACTORS:
        if not ignore_measure_gate and required_measures and not (measures & required_measures):
            continue
        if not predicate(url, authority):
            continue
        try:
            raw = extractor(url, authority)
            if raw:
                logger.info("[dispatch] %s: %d records via %s", authority, len(raw), label)
                results.extend(_coerce_records(raw))
        except Exception as exc:
            logger.warning("[dispatch] %s: %s extractor failed: %s", authority, label, exc)
            raise
    return results


def _resolve_handlers(measures: set[str]) -> list[tuple[str, Handler]]:
    seen: set[str] = set()
    resolved: list[tuple[str, Handler]] = []
    for measure in measures:
        key = MEASURE_ALIASES.get(measure, measure)
        if key in seen or key not in _MEASURE_HANDLERS:
            continue
        seen.add(key)
        resolved.append((key, _MEASURE_HANDLERS[key]))
    return resolved


def dispatch_target(
    ctx: TargetContext,
    *,
    url_extractor: Callable[[str, str], list[Any]] | None = None,
) -> TargetScrapeResult:
    ensure_registered()
    """Dispatch one Target. Returns records + explicit had_error flag.

    had_error is set on any exception in URL extractors, measure handlers, or fallbacks.
    waf_blocked and no_public_list short-circuit with had_error=False.
    Partial records + had_error=True is possible and intentional (upsert what you can;
    unconfirmed records for the authority will be labelled SCRAPE_FAILED in diff).
    """
    if "waf_blocked" in ctx.measures:
        logger.warning(
            "SKIPPING %s — waf_blocked (Akamai IP-range block; "
            "robots.txt unreachable; manual browser inspection required). "
            "See TARGETS.md notes.",
            ctx.authority,
        )
        return TargetScrapeResult(authority=ctx.authority, records=[], had_error=False)

    results: list[Record] = []
    ran_any = False
    had_error = False

    def _note_error() -> None:
        nonlocal had_error
        had_error = True

    # URL extraction layer (bloom, pdf, …)
    try:
        if url_extractor is not None:
            ext_records = _coerce_records(url_extractor(ctx.url, ctx.authority))
        else:
            ext_records = _run_url_extractors(ctx.url, ctx.authority, ctx.measures)
        if ext_records:
            results.extend(ext_records)
            ran_any = True
    except Exception as exc:
        _note_error()
        partial = getattr(exc, "partial", None) or []
        if partial:
            results.extend(_coerce_records(partial))
            ran_any = True
        logger.warning(
            "[dispatch] %s: URL extraction failed (%s) — continuing to measure handlers",
            ctx.authority,
            exc,
        )

    # Named-measure adapters (always coerce to dict at the Listing boundary — #801)
    for measure_name, handler in _resolve_handlers(ctx.measures):
        try:
            recs = _coerce_records(handler(ctx))
            results.extend(recs)
            ran_any = True
            logger.info("[dispatch] %s: %s → %d records", ctx.authority, measure_name, len(recs))
        except Exception as exc:
            _note_error()
            # SourceFetchError may carry partial pages already scraped
            partial = getattr(exc, "partial", None) or []
            if partial:
                results.extend(_coerce_records(partial))
                ran_any = True
            logger.warning("[dispatch] %s: %s failed: %s", ctx.authority, measure_name, exc)

    unknown = ctx.measures - KNOWN_MEASURES
    if unknown:
        logger.warning(
            "[dispatch] %s: unrecognised measures %s — check TARGETS.md", ctx.authority, unknown
        )

    if not ran_any and not results:
        logger.warning(
            "[dispatch] %s: no adapters or extractors matched; returning empty (no generic fallback)",
            ctx.authority,
        )

    return TargetScrapeResult(authority=ctx.authority, records=results, had_error=had_error)


# ---------------------------------------------------------------------------
# URL extractor registrations (bloom, pdf)
# ---------------------------------------------------------------------------


def _bloom_predicate(url: str, _authority: str) -> bool:
    from housing_list_search.extraction.bloom_housing import is_bloom_url

    return is_bloom_url(url)


def _bloom_extract(url: str, authority: str) -> list[Any]:
    from housing_list_search.extraction.bloom_housing import extract_bloom_for_target

    return extract_bloom_for_target(url, authority)


def _pdf_predicate(url: str, _authority: str) -> bool:
    u = (url or "").lower()
    return u.endswith(".pdf") or "documentcenter/view" in u or "documentcenter" in u


def _pdf_extract(url: str, authority: str) -> list[Any]:
    from housing_list_search.extraction.pdf import extract_records_from_pdf

    auth_label = authority or "City of Gilroy"
    return extract_records_from_pdf(url, authority=auth_label)


# URL extractors registered at import (lightweight predicates)
register_url_extractor("bloom", _bloom_predicate, _bloom_extract, measures=frozenset({"bloom"}))
register_url_extractor("pdf", _pdf_predicate, _pdf_extract, measures=frozenset({"pdf"}))


# Measure handler registrations (moved to lazy to avoid import-time side effects — #992)
_registered = False


def ensure_registered() -> None:
    """Idempotent measure-handler registration (lazy, once per process).

    #1054: tests that monkeypatch ``_MEASURE_HANDLERS`` must call this *before*
    patching so registration does not overwrite the mock on first dispatch.
    """
    global _registered
    if _registered:
        return
    _register_measure_handlers()
    _registered = True


def _reset_registration_for_tests() -> None:
    """Test helper: allow re-registration after clearing handlers."""
    global _registered
    _registered = False


# measure → (module path, Handler attribute). Handler is always ``run(ctx)``.
_HANDLER_SPECS: tuple[tuple[str, str, str], ...] = (
    ("john_stewart", "housing_list_search.adapters.john_stewart", "run"),
    ("gis", "housing_list_search.adapters.gis_extraction", "run"),
    ("housekeys", "housing_list_search.adapters.housekeys", "run"),
    ("civicplus", "housing_list_search.adapters.civicplus", "run"),
    ("alta", "housing_list_search.adapters.alta", "run"),
    ("charities_housing", "housing_list_search.adapters.charities_housing", "run"),
    ("midpen", "housing_list_search.adapters.midpen", "run"),
    ("eden", "housing_list_search.adapters.eden", "run"),
    ("eah", "housing_list_search.adapters.eah", "run"),
    ("first_housing", "housing_list_search.adapters.first_housing", "run"),
)


def _register_measure_handlers() -> None:
    """Register each platform Adapter's ``run(TargetContext)`` port — no lambda peel."""
    for measure, module_path, attr in _HANDLER_SPECS:
        try:
            mod = importlib.import_module(module_path)
            handler = getattr(mod, attr)
        except (ImportError, AttributeError) as exc:
            logger.error(
                "Adapter registration failed for measure=%s module=%s: %s",
                measure,
                module_path,
                exc,
            )
            continue
        register_measure(measure, handler)


# Registration is now lazy via ensure_registered() — no import side effects.


# ---------------------------------------------------------------------------
# Collapsed seam: primary scrape entry points (scrape_target / run_target now live here)
# ---------------------------------------------------------------------------


# (no __all__ to allow full public exports from the module)


def scrape_target(target: dict[str, Any]) -> TargetScrapeResult:
    """Primary entry point for orchestration (deepened/collapsed Target Scrape Result seam).

    Returns TargetScrapeResult (authority + raw records + had_error).
    This is the clean seam (no phantom list mutation, self-describing outcome).
    """
    ensure_registered()
    measures = parse_target_measures(target.get("scraping_measures") or "")

    ctx = TargetContext(
        authority=target.get("authority", ""),
        url=target.get("url", ""),
        measures=measures,
        administrator=target.get("administrator") or "",
        administrator_url=target.get("administrator_url") or "",
        administrator_phone=target.get("administrator_phone") or "",
        administrator_contact=target.get("administrator_contact") or "",
        notes=target.get("notes") or "",
    )

    return dispatch_target(ctx)
