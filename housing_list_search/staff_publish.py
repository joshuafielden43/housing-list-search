"""
staff_publish.py — deep Staff Publish module (#1063).

Owns post-persist staff artifact *policy*: partial vs full, when to rewrite
run_prev, changelog stubs, daily_summary paths, RUN_EVENT, Needs Review surface.

Does not own: Disappearance math (disappearance.py), summary markdown body
(outputs.py), or SQLite DDL (db.py). Callers: RunPipeline publish phase.
"""

from __future__ import annotations

import csv
import logging
from dataclasses import dataclass, field
from typing import Any

from housing_list_search.changelog import generate_changelog
from housing_list_search.csv_safety import sanitize_csv_field
from housing_list_search.db import DEFAULT_STALE_WARN_THRESHOLD, DatabaseManager
from housing_list_search.machine_persist import DIFF_CSV
from housing_list_search.needs_review import (
    CollectReview,
    authorities_unreliable_for_disappearance,
    build_run_review,
    should_update_disappearance_baseline,
    surface_run_review,
)
from housing_list_search.outputs import (
    PARTIAL_DAILY_SUMMARY_PATH,
    STAFF_DAILY_SUMMARY_PATH,
    generate_daily_summary,
    write_proposed_prune,
)

logger = logging.getLogger("housing_list_search")

CHANGELOG_MD = "changelog_diffs.md"
CHANGELOG_CSV = "changelog_diffs.csv"


@dataclass
class StaffPublishInput:
    """Everything Staff Publish needs after collect + persist."""

    listings: list[dict]
    run_id: str
    targets_attempted: int
    failed_targets: list[str] = field(default_factory=list)
    suspicious_zero_authorities: list[str] = field(default_factory=list)
    reverification_due_authorities: list[str] = field(default_factory=list)
    low_yield: list[tuple[str, int]] = field(default_factory=list)
    stale_n: int = 0
    scrape_failed_n: int = 0
    cov_property: int = 0
    cov_portal: int = 0
    cov_program: int = 0
    inserted: int = 0
    updated: int = 0
    skipped: list[tuple[str, str]] = field(default_factory=list)
    partial_run: bool = False
    target_filter: str | None = None


def write_partial_changelog_stubs(target_filter: str | None = None) -> None:
    """Partial --target run: mark changelog as non-global; leave run_prev alone."""
    with open(CHANGELOG_MD, "w", encoding="utf-8") as f:
        f.write("# Housing List Changelog\n\n")
        f.write(
            f"Partial --target run for {(target_filter or '')!r}; "
            "global run_prev.csv baseline was not updated.\n"
        )
    with open(CHANGELOG_CSV, "w", newline="", encoding="utf-8") as f:
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


def publish_staff_run(inp: StaffPublishInput, *, db: DatabaseManager) -> None:
    """
    Publish staff-facing artifacts for one Run.

    - Partial: stub changelog, partial daily_summary, preserve global run_prev
    - Full success: changelog (update run_prev), log_full_run, staff summary
    - Full with failed / low-yield / suspicious-zero targets: changelog without
      updating run_prev; skip log_full_run so previous successful run_id remains
      disappearance baseline; label unconfirmed rows SCRAPE_FAILED not REMOVED
      (down ≠ gone; soft-thin ≠ gone; #1085 / #238)
    """
    run_review = build_run_review(
        CollectReview(
            suspicious_zero_authorities=inp.suspicious_zero_authorities,
            reverification_due_authorities=inp.reverification_due_authorities,
            low_yield=inp.low_yield,
        ),
        stale_n=inp.stale_n,
        scrape_failed_n=inp.scrape_failed_n,
        stale_warn_threshold=DEFAULT_STALE_WARN_THRESHOLD,
    )
    unreliable = authorities_unreliable_for_disappearance(
        failed_targets=inp.failed_targets,
        low_yield=inp.low_yield,
        suspicious_zero_authorities=inp.suspicious_zero_authorities,
    )
    run_stats: dict[str, Any] = {
        "targets_attempted": inp.targets_attempted,
        "targets_succeeded": inp.targets_attempted - len(inp.failed_targets),
        "failed_authorities": list(inp.failed_targets),
        **run_review.to_run_stats_fields(),
    }

    logger.info(
        "RUN_EVENT run_id=%s targets=%d failed=%d property=%d portal=%d program=%d "
        "stale=%d scrape_failed=%d suspicious_zero=%d low_yield=%d partial=%s",
        inp.run_id,
        inp.targets_attempted,
        len(inp.failed_targets),
        inp.cov_property,
        inp.cov_portal,
        inp.cov_program,
        inp.stale_n,
        inp.scrape_failed_n,
        len(inp.suspicious_zero_authorities),
        len(inp.low_yield),
        inp.partial_run,
    )

    surface_run_review(run_review, run_id=inp.run_id)

    if inp.partial_run:
        write_partial_changelog_stubs(inp.target_filter)
        generate_daily_summary(
            inp.listings,
            skipped_targets=[],
            output_path=PARTIAL_DAILY_SUMMARY_PATH,
            run_stats=run_stats,
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
    update_baseline = should_update_disappearance_baseline(
        failed_targets=inp.failed_targets,
        low_yield=inp.low_yield,
        suspicious_zero_authorities=inp.suspicious_zero_authorities,
    )
    generate_changelog(
        inp.listings,
        skipped_targets=inp.skipped,
        run_id=inp.run_id,
        previous_run_id=previous_run_id,
        scrape_failed_authorities=unreliable,
        update_run_prev=update_baseline,
    )
    if not update_baseline:
        logger.warning(
            "Preserved prior run_prev.csv baseline — unreliable inventory signal(s): "
            "failed=%d low_yield=%d suspicious_zero=%d; incomplete scrape is not "
            "evidence of inventory removal (#1085/#238)",
            len(inp.failed_targets),
            len(inp.low_yield),
            len(inp.suspicious_zero_authorities),
        )
        # #1085 / #238: do not log_full_run when inventory is unproven — advancing
        # previous_full_run_id would let soft-thin days promote live housing to REMOVED.
        logger.warning(
            "Skipped log_full_run for run_id=%s — unreliable authorities=%s; "
            "previous successful full run_id remains the disappearance baseline",
            inp.run_id,
            ", ".join(unreliable) if unreliable else "(none)",
        )
    else:
        db.log_full_run(
            inp.run_id,
            rows_after=inp.inserted + inp.updated,
        )
    generate_daily_summary(
        inp.listings,
        skipped_targets=inp.skipped,
        run_stats=run_stats,
    )
    # #240: operator prune cheat-sheet (full runs only; never auto-deletes)
    write_proposed_prune(
        run_id=inp.run_id,
        stale_n=inp.stale_n,
        scrape_failed_n=inp.scrape_failed_n,
        diff_path=DIFF_CSV,
    )
