"""
pipeline.py — Run orchestration for the daily scrape loop.

#782 / #1070: RunPipeline.run() is a thin spine: collect → Machine Persist → Staff Publish.
cli.main() parses arguments and delegates here. Tests inject run_target_fn.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import housing_list_search.dispatch as dispatch_module
from housing_list_search.db import DatabaseManager
from housing_list_search.dispatch import TargetScrapeResult
from housing_list_search.listing import canonical_authority
from housing_list_search.machine_persist import PersistResult, persist_run
from housing_list_search.needs_review import assess_collect_review
from housing_list_search.staff_publish import StaffPublishInput, publish_staff_run

logger = logging.getLogger("housing_list_search")

TargetFn = Callable[
    [dict[str, Any]], TargetScrapeResult
]  # the deepened seam: always rich outcome with authority + raw records + had_error

_DEFAULT_MAX_TARGET_WORKERS = 3
_MAX_TARGET_WORKERS_CAP = 8


def max_target_workers() -> int:
    """Bounded parallelism for independent target scrapes (env: HLS_MAX_TARGET_WORKERS)."""
    raw = os.environ.get("HLS_MAX_TARGET_WORKERS", str(_DEFAULT_MAX_TARGET_WORKERS))
    try:
        n = int(raw)
    except ValueError:
        n = _DEFAULT_MAX_TARGET_WORKERS
    return max(1, min(n, _MAX_TARGET_WORKERS_CAP))


@dataclass
class RunResult:
    listings: list[dict] = field(default_factory=list)
    run_id: str = ""
    inserted: int = 0
    updated: int = 0
    n_full: int = 0
    n_diff: int = 0
    diff_counts: dict[str, int] = field(default_factory=dict)
    failed_targets: list[str] = field(default_factory=list)
    suspicious_zero_authorities: list[str] = field(default_factory=list)
    reverification_due_authorities: list[str] = field(default_factory=list)
    targets_attempted: int = 0
    partial_run: bool = False
    scrape_failed_n: int = 0
    stale_n: int = 0


@dataclass
class _CollectResult:
    """Raw scrape outcomes before Listing canonicalization (#782 collect phase)."""

    listings_raw: list[dict]
    failed_targets: list[str]
    listings_by_authority: dict[str, list[dict]]
    suspicious_zero_authorities: list[str]
    reverification_due_authorities: list[str]
    low_yield: list[tuple[str, int]]


class RunPipeline:
    """Orchestrates collect → Machine Persist → Staff Publish (#782 / #1070)."""

    def run(
        self,
        targets: list[dict[str, Any]],
        *,
        db: DatabaseManager,
        partial_run: bool = False,
        target_filter: str | None = None,
        skipped_targets: list[tuple[str, str]] | None = None,
        run_target_fn: TargetFn | None = None,
        run_id: str | None = None,
    ) -> RunResult:
        scrape = run_target_fn or dispatch_module.scrape_target
        skipped = skipped_targets or []
        target_authorities = (
            [canonical_authority(t["authority"]) or t["authority"] for t in targets]
            if partial_run
            else None
        )
        run_id = run_id or datetime.now().strftime("%Y%m%dT%H%M%S")

        # #1094: heartbeat before collect so logs show run_id if process is SIGKILL'd mid-scrape
        logger.info(
            "COLLECT_START run_id=%s targets=%d workers=%d partial=%s",
            run_id,
            len(targets),
            max_target_workers(),
            partial_run,
        )

        collected = self._collect(targets, scrape)
        persisted = persist_run(
            collected.listings_raw,
            db=db,
            run_id=run_id,
            target_authorities=target_authorities,
            failed_targets=collected.failed_targets,
        )
        self._publish(
            persisted,
            collected,
            db=db,
            targets=targets,
            skipped=skipped,
            partial_run=partial_run,
            target_filter=target_filter,
        )

        if collected.failed_targets:
            logger.error(
                "%d active target(s) failed this run: %s",
                len(collected.failed_targets),
                ", ".join(collected.failed_targets),
            )

        return RunResult(
            listings=persisted.listings,
            run_id=persisted.run_id,
            inserted=persisted.inserted,
            updated=persisted.updated,
            n_full=persisted.n_full,
            n_diff=persisted.n_diff,
            diff_counts=persisted.diff_counts,
            failed_targets=collected.failed_targets,
            suspicious_zero_authorities=collected.suspicious_zero_authorities,
            reverification_due_authorities=collected.reverification_due_authorities,
            targets_attempted=len(targets),
            partial_run=partial_run,
            scrape_failed_n=persisted.scrape_failed_n,
            stale_n=persisted.stale_n,
        )

    def _collect(self, targets: list[dict[str, Any]], scrape: TargetFn) -> _CollectResult:
        """Scrape all targets; compute suspicious zero / low-yield (pre-Listing)."""
        try:
            all_listings, failed_targets, listings_by_authority = self._scrape_targets(
                targets, scrape
            )
        finally:
            try:
                from housing_list_search.access import shutdown_playwright

                shutdown_playwright()
            except Exception:
                pass

        # RunReview collect phase (#1061) — composition + logs live in needs_review
        collect_review = assess_collect_review(
            targets, listings_by_authority, failed_targets
        )

        return _CollectResult(
            listings_raw=all_listings,
            failed_targets=failed_targets,
            listings_by_authority=listings_by_authority,
            suspicious_zero_authorities=collect_review.suspicious_zero_authorities,
            reverification_due_authorities=collect_review.reverification_due_authorities,
            low_yield=collect_review.low_yield,
        )

    def _publish(
        self,
        persisted: PersistResult,
        collected: _CollectResult,
        *,
        db: DatabaseManager,
        targets: list[dict[str, Any]],
        skipped: list[tuple[str, str]],
        partial_run: bool,
        target_filter: str | None,
    ) -> None:
        """Delegate staff artifact policy to Staff Publish (#1063)."""
        publish_staff_run(
            StaffPublishInput(
                listings=persisted.listings,
                run_id=persisted.run_id,
                targets_attempted=len(targets),
                failed_targets=collected.failed_targets,
                suspicious_zero_authorities=collected.suspicious_zero_authorities,
                reverification_due_authorities=collected.reverification_due_authorities,
                low_yield=collected.low_yield,
                stale_n=persisted.stale_n,
                scrape_failed_n=persisted.scrape_failed_n,
                cov_property=persisted.cov_property,
                cov_portal=persisted.cov_portal,
                cov_program=persisted.cov_program,
                inserted=persisted.inserted,
                updated=persisted.updated,
                skipped=skipped,
                partial_run=partial_run,
                target_filter=target_filter,
            ),
            db=db,
        )

    @staticmethod
    def _scrape_targets(
        targets: list[dict[str, Any]],
        scrape: TargetFn,
    ) -> tuple[list[dict], list[str], dict[str, list[dict]]]:
        """Collect raw results from all Targets via TargetScrapeResult outcomes."""
        workers = max_target_workers()
        if workers > 1 and len(targets) > 1:
            logger.info("Scraping %d targets with up to %d parallel workers", len(targets), workers)

        def _run_one(target: dict[str, Any]) -> TargetScrapeResult:
            authority = target.get("authority", "")
            try:
                res = scrape(target)
                if isinstance(res, TargetScrapeResult):
                    return res
                return TargetScrapeResult(authority=authority, records=res or [], had_error=False)
            except Exception as exc:
                logger.error("Error on %s: %s", authority, exc)
                return TargetScrapeResult(authority=authority, records=[], had_error=True)

        outcomes: list[TargetScrapeResult] = []
        if workers == 1 or len(targets) <= 1:
            for t in targets:
                outcomes.append(_run_one(t))
        else:
            with ThreadPoolExecutor(max_workers=workers) as pool:
                futures = [pool.submit(_run_one, t) for t in targets]
                for fut in as_completed(futures):
                    outcomes.append(fut.result())

        all_listings: list[dict] = []
        listings_by_authority: dict[str, list[dict]] = {}
        failed_targets: list[str] = []
        seen_failed: set[str] = set()
        for o in outcomes:
            if o.had_error:
                label = canonical_authority(o.authority) or o.authority
                if label and label not in seen_failed:
                    seen_failed.add(label)
                    failed_targets.append(label)
        for o in outcomes:
            all_listings.extend(o.records)
            listings_by_authority[o.authority] = o.records
        return all_listings, failed_targets, listings_by_authority
