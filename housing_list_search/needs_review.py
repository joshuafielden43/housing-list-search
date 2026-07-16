"""
needs_review.py — deep RunReview / Needs Review spine (#1061).

Assesses operator signals for a Run and surfaces them without failing the run
(ADR-0002 / ADR-0004):

  assess_collect_review(...)  → CollectReview  (zeros, reverify due, low-yield)
  build_run_review(...)       → RunReview      (+ STALE / SCRAPE_FAILED counts)
  surface_run_review(plan)    → log · webhook · Vikunja

Detection helpers stay in suspicious_zero / validated_zero; this module owns
composition, low-yield, notify policy, and transport adapters.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass, field
from datetime import date
from typing import Any

from housing_list_search.access import URLPolicyError, polite_post, validate_http_url
from housing_list_search.coverage import classify_record_kind
from housing_list_search.db import DEFAULT_STALE_WARN_THRESHOLD
from housing_list_search.measure_registry import expects_property_inventory, parse_target_measures
from housing_list_search.suspicious_zero import find_suspicious_zeros
from housing_list_search.validated_zero import find_reverification_due

logger = logging.getLogger(__name__)

_WEBHOOK_ENV = "HLS_NEEDS_REVIEW_WEBHOOK"
_DEFAULT_LOW_YIELD_THRESHOLD = 3

# Soft floors for large known inventory measures (#1083 / #238).
# Half-broken CSS/HTML often still returns a few cards — absolute threshold of 3
# never catches that. Floors sit under ground_truth.yaml min_records (~70%) for
# single-purpose county portfolios so thin scrapes fire low-yield without
# constant false alarms. bloom/gis/john_stewart stay conservative: same measure
# covers both large portfolios and thin city-filtered targets.
_INVENTORY_FLOOR_BY_MEASURE: dict[str, int] = {
    "midpen": 25,  # GT min 35; full ~46
    "charities_housing": 28,  # GT min 40; full ~48
    "eden": 18,  # GT min 25; full ~36
    "eah": 14,  # GT min 20; full ~27
    "first_housing": 10,  # GT min 15; full ~21
    "bloom": 15,  # San José large; Santa Clara city filter thin — keep modest
    "gis": 10,  # Sunnyvale large; Cupertino small
    "john_stewart": 15,  # jsco large; SCCHA directory smaller
    "alta": 8,
}


# ---------------------------------------------------------------------------
# Value types
# ---------------------------------------------------------------------------


@dataclass(frozen=True)
class CollectReview:
    """Operator signals known after scrape collect (before persist)."""

    suspicious_zero_authorities: list[str] = field(default_factory=list)
    reverification_due_authorities: list[str] = field(default_factory=list)
    low_yield: list[tuple[str, int]] = field(default_factory=list)


@dataclass(frozen=True)
class RunReview:
    """Full Needs Review plan for a Run — assess once, surface once."""

    suspicious_zero_authorities: list[str] = field(default_factory=list)
    reverification_due_authorities: list[str] = field(default_factory=list)
    low_yield: list[tuple[str, int]] = field(default_factory=list)
    stale_n: int = 0
    scrape_failed_n: int = 0
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD

    @property
    def needs_attention(self) -> bool:
        """True when any operator signal warrants Needs Review transport."""
        return should_notify_needs_review(
            suspicious_zero_authorities=self.suspicious_zero_authorities,
            reverification_due_authorities=self.reverification_due_authorities,
            low_yield=self.low_yield,
            stale_n=self.stale_n,
            scrape_failed_n=self.scrape_failed_n,
            stale_warn_threshold=self.stale_warn_threshold,
        )

    def to_run_stats_fields(self) -> dict[str, Any]:
        """Fields mergeable into pipeline run_stats / daily_summary."""
        return {
            "suspicious_zero_authorities": list(self.suspicious_zero_authorities),
            "reverification_due_authorities": list(self.reverification_due_authorities),
            "stale_n": self.stale_n,
            "scrape_failed_n": self.scrape_failed_n,
            "stale_warn_threshold": self.stale_warn_threshold,
            "low_yield": list(self.low_yield),
        }


# ---------------------------------------------------------------------------
# Assess (compose detectors + low-yield)
# ---------------------------------------------------------------------------


def low_yield_threshold() -> int:
    """Global floor: warn when inventory target returns fewer property rows (#789)."""
    raw = os.environ.get("HLS_LOW_YIELD_THRESHOLD", str(_DEFAULT_LOW_YIELD_THRESHOLD))
    try:
        return max(0, int(raw))
    except ValueError:
        return _DEFAULT_LOW_YIELD_THRESHOLD


def inventory_floor_for_measures(measures: set[str]) -> int:
    """Per-measure soft floor for large portfolios (#1083); 0 if none apply."""
    floors = [_INVENTORY_FLOOR_BY_MEASURE[m] for m in measures if m in _INVENTORY_FLOOR_BY_MEASURE]
    return max(floors) if floors else 0


def find_low_yield_targets(
    targets: list[dict[str, Any]],
    listings_by_authority: dict[str, list],
    failed_targets: list[str],
    suspicious_zero_authorities: list[str],
) -> list[tuple[str, int]]:
    """Inventory measures with 0 < property_count < effective floor (not failed / not zero).

    Effective floor = max(global HLS_LOW_YIELD_THRESHOLD, measure portfolio floor).
    Measure floors catch half-broken CSS on 40–70 property vendors (#1083).
    """
    global_thr = low_yield_threshold()
    failed = set(failed_targets)
    zeroed = set(suspicious_zero_authorities)
    out: list[tuple[str, int]] = []
    for t in targets:
        auth = (t.get("authority") or "").strip()
        if not auth or auth in failed or auth in zeroed:
            continue
        measures = parse_target_measures(t.get("scraping_measures") or "")
        if not expects_property_inventory(measures):
            continue
        thr = max(global_thr, inventory_floor_for_measures(measures))
        if thr <= 0:
            continue
        recs = listings_by_authority.get(auth) or []
        prop_n = sum(1 for r in recs if classify_record_kind(r) == "property")
        if 0 < prop_n < thr:
            out.append((auth, prop_n))
    return out


# Compat alias used by older tests
_find_low_yield_targets = find_low_yield_targets


def authorities_unreliable_for_disappearance(
    *,
    failed_targets: list[str] | None = None,
    low_yield: list[tuple[str, int]] | None = None,
    suspicious_zero_authorities: list[str] | None = None,
) -> list[str]:
    """Authorities whose unconfirmed rows must not become staff REMOVED (#238).

    Hard fails already flow through failed_targets. Soft-thin (low_yield) and
    empty inventory (suspicious_zero) look like success to collect but still
    mean prior portfolio is unproven this run — treat them like scrape failure
    for disappearance labels and freeze the disappearance baseline.
    """
    from housing_list_search.listing import canonical_authority

    out: list[str] = []
    seen: set[str] = set()

    def _add(raw: str) -> None:
        label = (canonical_authority(raw) or raw or "").strip()
        if label and label not in seen:
            seen.add(label)
            out.append(label)

    for a in failed_targets or []:
        _add(a)
    for a, _n in low_yield or []:
        _add(a)
    for a in suspicious_zero_authorities or []:
        _add(a)
    return out


def should_update_disappearance_baseline(
    *,
    failed_targets: list[str] | None = None,
    low_yield: list[tuple[str, int]] | None = None,
    suspicious_zero_authorities: list[str] | None = None,
) -> bool:
    """False when any authority's inventory is incomplete or failed (#238 / #1085)."""
    return not bool(
        authorities_unreliable_for_disappearance(
            failed_targets=failed_targets,
            low_yield=low_yield,
            suspicious_zero_authorities=suspicious_zero_authorities,
        )
    )


def assess_collect_review(
    targets: list[dict[str, Any]],
    listings_by_authority: dict[str, list[dict[str, Any]]],
    failed_targets: list[str],
    *,
    today: date | None = None,
    log: bool = True,
) -> CollectReview:
    """Compose collect-phase operator signals (does not fail the Run)."""
    suspicious = find_suspicious_zeros(
        targets, listings_by_authority, failed_targets, today=today
    )
    reverify = find_reverification_due(targets, today=today)
    low_yield = find_low_yield_targets(
        targets, listings_by_authority, failed_targets, suspicious
    )
    review = CollectReview(
        suspicious_zero_authorities=list(suspicious),
        reverification_due_authorities=list(reverify),
        low_yield=list(low_yield),
    )
    if log:
        log_collect_review(review)
    return review


def log_collect_review(review: CollectReview) -> None:
    """Emit collect-phase WARNING logs for zeros / reverify / low-yield."""
    if review.suspicious_zero_authorities:
        logger.warning(
            "%d suspicious zero(s) — property-inventory target(s) returned no property "
            "records: %s",
            len(review.suspicious_zero_authorities),
            ", ".join(review.suspicious_zero_authorities),
        )
    if review.reverification_due_authorities:
        logger.warning(
            "%d Validated Zero(s) past review date — reverification due: %s",
            len(review.reverification_due_authorities),
            ", ".join(review.reverification_due_authorities),
        )
    if review.low_yield:
        logger.warning(
            "%d low-yield inventory target(s) (possible silent partial scrape): %s",
            len(review.low_yield),
            ", ".join(f"{a}={n}" for a, n in review.low_yield),
        )


def build_run_review(
    collect: CollectReview,
    *,
    stale_n: int = 0,
    scrape_failed_n: int = 0,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> RunReview:
    """Attach persist-phase integrity counts to collect signals."""
    return RunReview(
        suspicious_zero_authorities=list(collect.suspicious_zero_authorities),
        reverification_due_authorities=list(collect.reverification_due_authorities),
        low_yield=list(collect.low_yield),
        stale_n=stale_n,
        scrape_failed_n=scrape_failed_n,
        stale_warn_threshold=stale_warn_threshold,
    )


def run_review_from_signals(
    *,
    suspicious_zero_authorities: list[str] | None = None,
    reverification_due_authorities: list[str] | None = None,
    low_yield: list[tuple[str, int]] | None = None,
    stale_n: int = 0,
    scrape_failed_n: int = 0,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> RunReview:
    """Build a RunReview from already-computed signal lists (tests / compat)."""
    return RunReview(
        suspicious_zero_authorities=list(suspicious_zero_authorities or []),
        reverification_due_authorities=list(reverification_due_authorities or []),
        low_yield=list(low_yield or []),
        stale_n=stale_n,
        scrape_failed_n=scrape_failed_n,
        stale_warn_threshold=stale_warn_threshold,
    )


# ---------------------------------------------------------------------------
# Notify policy + surface (transport adapters)
# ---------------------------------------------------------------------------


def should_notify_needs_review(
    *,
    suspicious_zero_authorities: list[str],
    reverification_due_authorities: list[str],
    low_yield: list[tuple[str, int]] | None = None,
    stale_n: int = 0,
    scrape_failed_n: int = 0,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> bool:
    """True when any Needs Review signal is present (#1083: low_yield pages)."""
    if suspicious_zero_authorities or reverification_due_authorities:
        return True
    if low_yield:
        return True
    if scrape_failed_n > 0:
        return True
    if stale_n >= stale_warn_threshold:
        return True
    return False


def surface_run_review(
    review: RunReview | None = None,
    *,
    run_id: str = "",
    suspicious_zero_authorities: list[str] | None = None,
    reverification_due_authorities: list[str] | None = None,
    low_yield: list[tuple[str, int]] | None = None,
    stale_n: int = 0,
    scrape_failed_n: int = 0,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> None:
    """Surface a RunReview: log NEEDS_REVIEW → webhook → Vikunja.

    Prefer ``surface_run_review(plan, run_id=...)``. Kwargs remain for callers
    that have not built a RunReview yet. Safe no-op when no signals fire
    (ADR-0004: does not fail the run).
    """
    if review is None:
        review = run_review_from_signals(
            suspicious_zero_authorities=suspicious_zero_authorities,
            reverification_due_authorities=reverification_due_authorities,
            low_yield=low_yield,
            stale_n=stale_n,
            scrape_failed_n=scrape_failed_n,
            stale_warn_threshold=stale_warn_threshold,
        )
    if not review.needs_attention:
        return

    payload: dict[str, Any] = {
        "run_id": run_id,
        "suspicious_zero_authorities": list(review.suspicious_zero_authorities),
        "reverification_due_authorities": list(review.reverification_due_authorities),
        "stale_n": review.stale_n,
        "scrape_failed_n": review.scrape_failed_n,
        "low_yield": [{"authority": a, "property_count": n} for a, n in review.low_yield],
    }

    logger.warning(
        "NEEDS_REVIEW run_id=%s suspicious_zero=%s reverification_due=%s "
        "stale_n=%s scrape_failed_n=%s low_yield=%s",
        run_id,
        review.suspicious_zero_authorities,
        review.reverification_due_authorities,
        review.stale_n,
        review.scrape_failed_n,
        review.low_yield,
    )

    _post_webhook(payload)
    _sync_vikunja(
        run_id=run_id,
        suspicious_zero_authorities=list(review.suspicious_zero_authorities),
        reverification_due_authorities=list(review.reverification_due_authorities),
        stale_n=review.stale_n,
        scrape_failed_n=review.scrape_failed_n,
    )


def notify_needs_review(
    *,
    run_id: str,
    suspicious_zero_authorities: list[str],
    reverification_due_authorities: list[str],
    low_yield: list[tuple[str, int]] | None = None,
    stale_n: int = 0,
    scrape_failed_n: int = 0,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> None:
    """Backward-compatible alias for surface_run_review (#783)."""
    surface_run_review(
        run_id=run_id,
        suspicious_zero_authorities=suspicious_zero_authorities,
        reverification_due_authorities=reverification_due_authorities,
        low_yield=low_yield,
        stale_n=stale_n,
        scrape_failed_n=scrape_failed_n,
        stale_warn_threshold=stale_warn_threshold,
    )


def _safe_operator_url(url: str, *, label: str) -> str | None:
    """Validate operator-configured egress URL; return None when policy blocks."""
    try:
        return validate_http_url(url.strip(), resolve_dns=False)
    except URLPolicyError as exc:
        logger.warning("%s URL blocked by policy: %s", label, exc)
        return None


def _post_webhook(payload: dict[str, Any]) -> None:
    webhook_raw = (os.environ.get(_WEBHOOK_ENV) or "").strip()
    webhook = _safe_operator_url(webhook_raw, label="Needs Review webhook") if webhook_raw else None
    if not webhook:
        return
    try:
        resp = polite_post(
            webhook,
            json=payload,
            headers={"Content-Type": "application/json"},
        )
        if resp is not None:
            resp.raise_for_status()
            logger.info("Posted Needs Review payload to webhook")
        else:
            logger.warning("Needs Review webhook POST blocked by policy")
    except Exception as exc:
        from urllib.parse import urlparse

        try:
            host = urlparse(webhook_raw).netloc or "webhook"
        except Exception:
            host = "webhook"
        logger.warning("Needs Review webhook POST failed for host=%s: %s", host, exc)


def _sync_vikunja(
    *,
    run_id: str,
    suspicious_zero_authorities: list[str],
    reverification_due_authorities: list[str],
    stale_n: int,
    scrape_failed_n: int,
) -> None:
    from housing_list_search.vikunja_reverification import sync_reverification_tasks

    sync_reverification_tasks(
        run_id=run_id,
        suspicious_zero_authorities=suspicious_zero_authorities,
        reverification_due_authorities=reverification_due_authorities,
        stale_n=stale_n,
        scrape_failed_n=scrape_failed_n,
    )
