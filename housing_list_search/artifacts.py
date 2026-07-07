"""
artifacts.py — deep module for post-Listing run artifact generation seam.

After the canonical Listing transformation (canonicalize_listings + dedupe),
DB upsert, and machine data exports (current_full.csv, diff.csv via db),
this is the single place that produces staff and operational artifacts:

- Full runs: changelog_diffs.* (via project_disappearance + renders), run_prev.csv
  snapshot, daily_summary.md, db.log_full_run
- Partial (--target) runs: partial changelog markers, daily_summary_partial.md
  (global baseline left unchanged)

Callers (RunPipeline) use the narrow generate_run_artifacts entry point.
Implementation coordinates changelog + outputs (and transitively disappearance,
freshness, coverage) without callers knowing the branching or snapshot rules.

Increases locality for disappearance semantics (ADR-0001), output formatting,
and run side-effects. Pairs with the Listing seam (input to this module is
always canonical rows) and the earlier Dispatch/TargetScrapeResult seam.

Deletion test: removing this module forces the partial/full branching,
generate calls, and snapshot coordination to scatter back into pipeline.py.

See CONTEXT.md (Freshness, Disappearance, Run) and AGENTS.md.
"""

from __future__ import annotations

import csv
from typing import Any

from housing_list_search.changelog import generate_changelog
from housing_list_search.csv_safety import sanitize_csv_field
from housing_list_search.db import DatabaseManager
from housing_list_search.outputs import (
    PARTIAL_DAILY_SUMMARY_PATH,
    generate_daily_summary,
)

# Note: STAFF_DAILY_SUMMARY_PATH and PARTIAL_ live in outputs.py (re-export here if a unified entrypoint is desired later).


def _write_partial_changelog(target_filter: str) -> None:
    """Marker files for partial runs (global run_prev baseline unchanged)."""
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


def generate_run_artifacts(
    listings: list[dict[str, Any]],
    *,
    db: DatabaseManager,
    run_id: str,
    partial_run: bool = False,
    target_filter: str | None = None,
    skipped_targets: list[tuple[str, str]] | None = None,
    scrape_failed_authorities: list[str] | None = None,
    run_stats: dict[str, Any] | None = None,
    rows_after: int = 0,
) -> None:
    """
    High-level seam for all post-canonical run artifacts.

    listings must be the deduplicated canonical rows (post Listing seam).
    Delegates to focused submodules for projection/render/coverage while
    owning the full vs partial decision and side effects (snapshot, log_full_run).
    """
    skipped = skipped_targets or []
    stats = run_stats or {}

    if partial_run:
        _write_partial_changelog(target_filter or "")
        generate_daily_summary(
            listings,
            skipped_targets=[],
            output_path=PARTIAL_DAILY_SUMMARY_PATH,
            run_stats=stats,
        )
    else:
        previous_run_id = db.get_previous_full_run_id()
        generate_changelog(
            listings,
            skipped_targets=skipped,
            run_id=run_id,
            previous_run_id=previous_run_id,
            scrape_failed_authorities=scrape_failed_authorities,
        )
        db.log_full_run(run_id, rows_after=rows_after)
        generate_daily_summary(
            listings,
            skipped_targets=skipped,
            run_stats=stats,
        )
