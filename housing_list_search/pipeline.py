"""
pipeline.py — Run orchestration for the daily scrape loop.

#782: RunPipeline.run() is a thin spine: collect → persist → publish.
cli.main() parses arguments and delegates here. Tests inject run_target_fn.
"""

from __future__ import annotations

import csv
import logging
import os
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import housing_list_search.dispatch as dispatch_module
from housing_list_search.changelog import generate_changelog
from housing_list_search.coverage import summarize_coverage
from housing_list_search.csv_safety import sanitize_csv_field
from housing_list_search.db import DEFAULT_STALE_WARN_THRESHOLD, DatabaseManager
from housing_list_search.dedupe import deduplicate_listings
from housing_list_search.dispatch import TargetScrapeResult
from housing_list_search.listing import canonical_authority, canonicalize_listings
from housing_list_search.needs_review import (
    CollectReview,
    assess_collect_review,
    build_run_review,
    surface_run_review,
)
from housing_list_search.outputs import (
    PARTIAL_DAILY_SUMMARY_PATH,
    STAFF_DAILY_SUMMARY_PATH,
    generate_daily_summary,
)

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


@dataclass
class _PersistResult:
    """DB + machine exports after canonicalize/dedupe (#782 persist phase)."""

    listings: list[dict]
    run_id: str
    inserted: int
    updated: int
    n_full: int
    n_diff: int
    diff_counts: dict[str, int]
    scrape_failed_n: int
    stale_n: int
    cov_property: int
    cov_portal: int
    cov_program: int
    cov_total: int


class RunPipeline:
    """Orchestrates collect → persist → publish (#782)."""

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

        collected = self._collect(targets, scrape)
        persisted = self._persist(
            collected,
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

    def _persist(
        self,
        collected: _CollectResult,
        *,
        db: DatabaseManager,
        run_id: str,
        target_authorities: list[str] | None,
        failed_targets: list[str],
    ) -> _PersistResult:
        """Canonicalize, dedupe, upsert, export machine CSVs."""
        all_listings = canonicalize_listings(collected.listings_raw)
        all_listings = deduplicate_listings(all_listings, canonical=True)

        counts = db.upsert_listings(all_listings, run_id=run_id)
        logger.info("DB upsert: %d inserted, %d updated", counts["inserted"], counts["updated"])

        cov = summarize_coverage(all_listings)
        logger.info(
            "Coverage: %d property, %d portal, %d program (%d total)",
            cov.property_count,
            cov.portal_count,
            cov.program_count,
            cov.total,
        )

        n_full = db.export_csv("current_full.csv", run_id=run_id)
        n_diff = db.export_diff_csv(
            "diff.csv",
            run_id=run_id,
            authorities=target_authorities,
            scrape_failed_authorities=failed_targets,
        )

        diff_counts = db.diff_counts(
            run_id,
            authorities=target_authorities,
            scrape_failed_authorities=failed_targets,
        )
        scrape_failed_n = diff_counts.get("SCRAPE_FAILED", 0)
        if scrape_failed_n:
            logger.warning(
                "%d SCRAPE_FAILED record(s) in diff.csv — scrape errors, not confirmed closures",
                scrape_failed_n,
            )

        stale_n = diff_counts.get("STALE", 0)
        if stale_n >= DEFAULT_STALE_WARN_THRESHOLD:
            logger.warning(
                "%d STALE record(s) in diff.csv (not confirmed this run; threshold=%d). "
                "Review diff.csv, then (safely) prune with: "
                "python scripts/db_manage.py prune --from-diff  [or --not-seen-since 45 after review]",
                stale_n,
                DEFAULT_STALE_WARN_THRESHOLD,
            )
        elif stale_n > 0:
            logger.info(
                "%d STALE record(s) in diff.csv (below warn threshold of %d)",
                stale_n,
                DEFAULT_STALE_WARN_THRESHOLD,
            )

        return _PersistResult(
            listings=all_listings,
            run_id=run_id,
            inserted=counts["inserted"],
            updated=counts["updated"],
            n_full=n_full,
            n_diff=n_diff,
            diff_counts=diff_counts,
            scrape_failed_n=scrape_failed_n,
            stale_n=stale_n,
            cov_property=cov.property_count,
            cov_portal=cov.portal_count,
            cov_program=cov.program_count,
            cov_total=cov.total,
        )

    def _publish(
        self,
        persisted: _PersistResult,
        collected: _CollectResult,
        *,
        db: DatabaseManager,
        targets: list[dict[str, Any]],
        skipped: list[tuple[str, str]],
        partial_run: bool,
        target_filter: str | None,
    ) -> None:
        """Staff artifacts, Needs Review surface, run history, structured RUN_EVENT."""
        run_review = build_run_review(
            CollectReview(
                suspicious_zero_authorities=collected.suspicious_zero_authorities,
                reverification_due_authorities=collected.reverification_due_authorities,
                low_yield=collected.low_yield,
            ),
            stale_n=persisted.stale_n,
            scrape_failed_n=persisted.scrape_failed_n,
            stale_warn_threshold=DEFAULT_STALE_WARN_THRESHOLD,
        )
        run_stats = {
            "targets_attempted": len(targets),
            "targets_succeeded": len(targets) - len(collected.failed_targets),
            "failed_authorities": collected.failed_targets,
            **run_review.to_run_stats_fields(),
        }

        logger.info(
            "RUN_EVENT run_id=%s targets=%d failed=%d property=%d portal=%d program=%d "
            "stale=%d scrape_failed=%d suspicious_zero=%d low_yield=%d partial=%s",
            persisted.run_id,
            len(targets),
            len(collected.failed_targets),
            persisted.cov_property,
            persisted.cov_portal,
            persisted.cov_program,
            persisted.stale_n,
            persisted.scrape_failed_n,
            len(collected.suspicious_zero_authorities),
            len(collected.low_yield),
            partial_run,
        )

        surface_run_review(run_review, run_id=persisted.run_id)

        if partial_run:
            with open("changelog_diffs.md", "w", encoding="utf-8") as f:
                f.write("# Housing List Changelog\n\n")
                f.write(
                    f"Partial --target run for {(target_filter or '')!r}; "
                    "global run_prev.csv baseline was not updated.\n"
                )
            with open("changelog_diffs.csv", "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["change_type", "authority", "property_name", "url", "details"])
                writer.writerow(
                    [
                        sanitize_csv_field("PARTIAL_RUN"),
                        sanitize_csv_field("target"),
                        sanitize_csv_field(target_filter or ""),
                        sanitize_csv_field(""),
                        sanitize_csv_field("global changelog baseline not updated"),
                    ]
                )
            generate_daily_summary(
                persisted.listings,
                skipped_targets=[],
                output_path=PARTIAL_DAILY_SUMMARY_PATH,
                run_stats=run_stats or {},
            )
            logger.info(
                "Partial --target run: left global run_prev.csv changelog baseline unchanged"
            )
            logger.info(
                "Partial --target run: wrote %s; left staff-facing %s unchanged",
                PARTIAL_DAILY_SUMMARY_PATH,
                STAFF_DAILY_SUMMARY_PATH,
            )
            return

        previous_run_id = db.get_previous_full_run_id()
        update_baseline = not bool(collected.failed_targets)
        generate_changelog(
            persisted.listings,
            skipped_targets=skipped,
            run_id=persisted.run_id,
            previous_run_id=previous_run_id,
            scrape_failed_authorities=collected.failed_targets,
            update_run_prev=update_baseline,
        )
        if not update_baseline:
            logger.warning(
                "Preserved prior run_prev.csv baseline — %d failed target(s); "
                "down/outage is not evidence of inventory removal",
                len(collected.failed_targets),
            )
        db.log_full_run(
            persisted.run_id,
            rows_after=persisted.inserted + persisted.updated,
        )
        generate_daily_summary(
            persisted.listings,
            skipped_targets=skipped,
            run_stats=run_stats or {},
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
