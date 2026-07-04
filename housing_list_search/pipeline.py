"""
pipeline.py — Run orchestration for the daily scrape loop.

cli.main() parses arguments and delegates here. Tests can call RunPipeline.run()
directly with a fake run_target_fn — no sys.argv or registry mocking required.
"""

from __future__ import annotations

import csv
import logging
import os
import threading
from collections.abc import Callable
from concurrent.futures import ThreadPoolExecutor, as_completed
from dataclasses import dataclass, field
from datetime import datetime
from typing import Any

import housing_list_search.runner as runner_module
from housing_list_search.changelog import generate_changelog
from housing_list_search.coverage import summarize_coverage
from housing_list_search.csv_safety import sanitize_csv_field
from housing_list_search.db import DEFAULT_STALE_WARN_THRESHOLD, DatabaseManager
from housing_list_search.dedupe import deduplicate_listings
from housing_list_search.outputs import (
    PARTIAL_DAILY_SUMMARY_PATH,
    STAFF_DAILY_SUMMARY_PATH,
    generate_daily_summary,
)
from housing_list_search.suspicious_zero import find_suspicious_zeros

logger = logging.getLogger("housing_list_search")

TargetFn = Callable[..., list[dict]]

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
    targets_attempted: int = 0
    partial_run: bool = False
    scrape_failed_n: int = 0
    stale_n: int = 0


class RunPipeline:
    """Orchestrates scrape → dedupe → persist → export → changelog/summary."""

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
        scrape = run_target_fn or runner_module.run_target
        skipped = skipped_targets or []
        target_authorities = [t["authority"] for t in targets] if partial_run else None

        all_listings, failed_targets, listings_by_authority = self._scrape_targets(targets, scrape)
        suspicious_zero_authorities = find_suspicious_zeros(
            targets, listings_by_authority, failed_targets
        )
        if suspicious_zero_authorities:
            logger.warning(
                "%d suspicious zero(s) — property-inventory target(s) returned no property "
                "records: %s",
                len(suspicious_zero_authorities),
                ", ".join(suspicious_zero_authorities),
            )

        all_listings = deduplicate_listings(all_listings)
        run_id = run_id or datetime.now().strftime("%Y%m%dT%H%M%S")

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

        n_full = db.export_csv("current_full.csv")
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
                "Review diff.csv, then prune when appropriate: "
                "python scripts/db_manage.py prune --not-seen-since 45",
                stale_n,
                DEFAULT_STALE_WARN_THRESHOLD,
            )
        elif stale_n > 0:
            logger.info(
                "%d STALE record(s) in diff.csv (below warn threshold of %d)",
                stale_n,
                DEFAULT_STALE_WARN_THRESHOLD,
            )

        run_stats = {
            "targets_attempted": len(targets),
            "targets_succeeded": len(targets) - len(failed_targets),
            "failed_authorities": failed_targets,
            "suspicious_zero_authorities": suspicious_zero_authorities,
        }

        if partial_run:
            self._write_partial_changelog(target_filter or "")
            logger.info(
                "Partial --target run: left global run_prev.csv changelog baseline unchanged"
            )
            generate_daily_summary(
                all_listings,
                skipped_targets=[],
                output_path=PARTIAL_DAILY_SUMMARY_PATH,
                run_stats=run_stats,
            )
            logger.info(
                "Partial --target run: wrote %s; left staff-facing %s unchanged",
                PARTIAL_DAILY_SUMMARY_PATH,
                STAFF_DAILY_SUMMARY_PATH,
            )
        else:
            generate_changelog(all_listings, skipped_targets=skipped)
            generate_daily_summary(
                all_listings,
                skipped_targets=skipped,
                run_stats=run_stats,
            )

        if failed_targets:
            logger.error(
                "%d active target(s) failed this run: %s",
                len(failed_targets),
                ", ".join(failed_targets),
            )

        return RunResult(
            listings=all_listings,
            run_id=run_id,
            inserted=counts["inserted"],
            updated=counts["updated"],
            n_full=n_full,
            n_diff=n_diff,
            diff_counts=diff_counts,
            failed_targets=failed_targets,
            suspicious_zero_authorities=suspicious_zero_authorities,
            targets_attempted=len(targets),
            partial_run=partial_run,
            scrape_failed_n=scrape_failed_n,
            stale_n=stale_n,
        )

    @staticmethod
    def _scrape_targets(
        targets: list[dict[str, Any]],
        scrape: TargetFn,
    ) -> tuple[list[dict], list[str], dict[str, list[dict]]]:
        all_listings: list[dict] = []
        listings_by_authority: dict[str, list[dict]] = {}
        failed_targets: list[str] = []
        failures_lock = threading.Lock()
        listings_lock = threading.Lock()
        workers = max_target_workers()
        if workers > 1 and len(targets) > 1:
            logger.info("Scraping %d targets with up to %d parallel workers", len(targets), workers)

        def _run_one(target: dict[str, Any]) -> None:
            authority = target.get("authority", "")
            local_failures: list[str] = []
            recs: list[dict] = []
            try:
                recs = scrape(target, failures=local_failures)
                with listings_lock:
                    all_listings.extend(recs)
                    listings_by_authority[authority] = recs
            except Exception as exc:
                logger.error("Error on %s: %s", authority, exc)
                local_failures.append(authority)
            if local_failures:
                with failures_lock:
                    for name in local_failures:
                        if name not in failed_targets:
                            failed_targets.append(name)

        if workers == 1 or len(targets) <= 1:
            for t in targets:
                _run_one(t)
            return all_listings, failed_targets, listings_by_authority

        with ThreadPoolExecutor(max_workers=workers) as pool:
            futures = [pool.submit(_run_one, t) for t in targets]
            for fut in as_completed(futures):
                fut.result()

        return all_listings, failed_targets, listings_by_authority

    @staticmethod
    def _write_partial_changelog(target_filter: str) -> None:
        with open("changelog_diffs.md", "w", encoding="utf-8") as f:
            f.write("# Housing List Changelog\n\n")
            f.write(
                f"Partial --target run for {target_filter!r}; "
                "global run_prev.csv baseline was not updated.\n"
            )
        with open("changelog_diffs.csv", "w", newline="", encoding="utf-8") as f:
            writer = csv.writer(f)
            writer.writerow(["change_type", "authority", "property_name", "url", "details"])
            writer.writerow(
                [
                    sanitize_csv_field("PARTIAL_RUN"),
                    sanitize_csv_field("target"),
                    sanitize_csv_field(target_filter),
                    sanitize_csv_field(""),
                    sanitize_csv_field("global changelog baseline not updated"),
                ]
            )
