#!/usr/bin/env python3
# cli.py - Housing List Aggregator CLI (importable from the package)

import argparse
import logging
import sys
from datetime import datetime


def main():
    parser = argparse.ArgumentParser(description="Housing Waitlist Aggregator (Santa Clara County)")
    parser.add_argument("--run", action="store_true", help="Normal daily scrape")
    parser.add_argument(
        "--target",
        metavar="AUTHORITY",
        help="With --run: process only targets whose authority contains this substring (case-insensitive)",
    )
    args = parser.parse_args()

    if args.run:
        print(f"=== Normal Run Started at {datetime.now()} ===")

        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)],
        )
        logger = logging.getLogger("housing_list_search")

        from housing_list_search.db import get_manager
        from housing_list_search.pipeline import RunPipeline
        from housing_list_search.registry import (
            get_active_targets,
            get_skipped_targets,
            load_targets_to_db,
        )
        from housing_list_search.staff_summary import (
            PARTIAL_DAILY_SUMMARY_PATH,
            STAFF_DAILY_SUMMARY_PATH,
        )
        from housing_list_search.target_filter import filter_targets_by_authority

        db = get_manager()
        db.init_db()
        load_targets_to_db()

        partial_run = bool(args.target)

        skipped_targets: list[tuple[str, str]] = []
        if not partial_run:
            for t in get_skipped_targets():
                authority = t["authority"]
                notes = t.get("notes") or ""
                logger.warning(
                    "SKIPPING %s — marked 'no_public_list' in TARGETS.md. "
                    "Remove the marker when a public structured source appears.",
                    authority,
                )
                skipped_targets.append((authority, notes[:200]))

        active = get_active_targets()
        if args.target:
            active = filter_targets_by_authority(active, args.target)
            if not active:
                print(f"⚠️  No active targets match --target {args.target!r}")
                sys.exit(1)
            print(f"\n🔄 Scraping {len(active)} target(s) matching {args.target!r}...")
        else:
            print("\n🔄 Scraping all targets...")

        for t in active:
            print(f"\n→ Processing: {t['authority']}")

        result = RunPipeline().run(
            active,
            db=db,
            partial_run=partial_run,
            target_filter=args.target,
            skipped_targets=skipped_targets,
        )

        full_csv = "current_full_partial.csv" if result.partial_run else "current_full.csv"
        diff_csv = "diff_partial.csv" if result.partial_run else "diff.csv"
        print(f"   Exported {full_csv} ({result.n_full} rows), {diff_csv} ({result.n_diff} rows)")
        print(
            f"\n✅ Run complete! {len(result.listings)} listings this run "
            f"({result.inserted} new, {result.updated} updated)."
        )
        if result.scrape_failed_n:
            diff_name = "diff_partial.csv" if partial_run else "diff.csv"
            print(
                f"   ⚠️  SCRAPE_FAILED: {result.scrape_failed_n} record(s) in {diff_name} "
                "(scrape errors, not confirmed closures)"
            )
        if result.stale_n:
            diff_name = "diff_partial.csv" if partial_run else "diff.csv"
            print(
                f"   ⚠️  STALE: {result.stale_n} record(s) in {diff_name} "
                "(not confirmed this run)"
            )
        if skipped_targets:
            print(f"   ⚠️  Skipped {len(skipped_targets)} targets marked no_public_list")
        if partial_run:
            print(
                "   Partial --target run: global run_prev / current_full.csv / diff.csv "
                "were not updated (#241)"
            )
            print(
                f"   Files: current_full_partial.csv  diff_partial.csv  "
                f"{PARTIAL_DAILY_SUMMARY_PATH}  changelog_diffs.md  "
                f"({STAFF_DAILY_SUMMARY_PATH} preserved)"
            )
        else:
            print(
                f"   Files: current_full.csv  diff.csv  {STAFF_DAILY_SUMMARY_PATH}  "
                "changelog_diffs.md"
            )

        if result.failed_targets:
            sys.exit(1)

    else:
        print("Usage:")
        print("  python main.py --run")
        print('  python main.py --run --target "San José"')
        print("\nTargets are hand-maintained in TARGETS.md.")
        sys.exit(0)


if __name__ == "__main__":
    main()
