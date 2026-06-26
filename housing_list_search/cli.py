#!/usr/bin/env python3
# cli.py - Housing List Aggregator CLI (importable from the package)

import argparse
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

        import csv
        import logging
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        logger = logging.getLogger("housing_list_search")

        from housing_list_search.registry import load_targets_to_db, get_active_targets, get_skipped_targets
        from housing_list_search.target_filter import filter_targets_by_authority
        from housing_list_search.runner import run_target
        from housing_list_search.dedupe import deduplicate_listings
        from housing_list_search.db import get_manager, DEFAULT_STALE_WARN_THRESHOLD
        from housing_list_search.changelog import generate_changelog
        from housing_list_search.outputs import generate_daily_summary

        load_targets_to_db()
        db = get_manager()
        db.init_db()

        partial_run = bool(args.target)

        # Log intentionally skipped targets (no_public_list) only during full runs.
        skipped_targets = []
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
        target_authorities = None
        if args.target:
            active = filter_targets_by_authority(active, args.target)
            if not active:
                print(f"⚠️  No active targets match --target {args.target!r}")
                sys.exit(1)
            target_authorities = [t["authority"] for t in active]
            print(f"\n🔄 Scraping {len(active)} target(s) matching {args.target!r}...")
        else:
            print("\n🔄 Scraping all targets...")
        all_listings = []

        for t in active:
            print(f"\n→ Processing: {t['authority']}")
            try:
                recs = run_target(t)
                all_listings.extend(recs)
            except Exception as exc:
                logger.error("Error on %s: %s", t["authority"], exc)

        # Deduplicate across sources
        all_listings = deduplicate_listings(all_listings)

        # Stable run identifier — shared between upsert and diff export so
        # NEW/UPDATED labels in diff.csv are based on run membership, not timestamps.
        run_id = datetime.now().strftime("%Y%m%dT%H%M%S")

        # Write through to DB — this is now the source of truth
        counts = db.upsert_listings(all_listings, run_id=run_id)
        logger.info("DB upsert: %d inserted, %d updated", counts["inserted"], counts["updated"])

        # Export CSV outputs from DB. Partial --target runs scope diff.csv so
        # unrelated authorities are not reported as stale.
        n_full = db.export_csv("current_full.csv")
        n_diff = db.export_diff_csv("diff.csv", run_id=run_id, authorities=target_authorities)
        print(f"   Exported current_full.csv ({n_full} rows), diff.csv ({n_diff} rows)")

        diff_counts = db.diff_counts(run_id, authorities=target_authorities)
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

        if partial_run:
            with open("changelog_diffs.md", "w", encoding="utf-8") as f:
                f.write("# Housing List Changelog\n\n")
                f.write(
                    f"Partial --target run for {args.target!r}; "
                    "global run_prev.csv baseline was not updated.\n"
                )
            with open("changelog_diffs.csv", "w", newline="", encoding="utf-8") as f:
                writer = csv.writer(f)
                writer.writerow(["change_type", "authority", "property_name", "details", "timestamp"])
                writer.writerow([
                    "PARTIAL_RUN", "target", args.target,
                    "global changelog baseline not updated",
                    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                ])
            logger.info("Partial --target run: left global run_prev.csv changelog baseline unchanged")
            generate_daily_summary(all_listings, skipped_targets=[])
        else:
            generate_changelog(all_listings, skipped_targets=skipped_targets)
            generate_daily_summary(all_listings, skipped_targets=skipped_targets)

        print(f"\n✅ Run complete! {len(all_listings)} listings this run "
              f"({counts['inserted']} new, {counts['updated']} updated).")
        if skipped_targets:
            print(f"   ⚠️  Skipped {len(skipped_targets)} targets marked no_public_list")
        if partial_run:
            print("   Partial --target run: global changelog baseline was not updated")
        print("   Files: current_full.csv  diff.csv  daily_summary.md  changelog_diffs.md")

    else:
        print("Usage:")
        print("  python main.py --run")
        print("  python main.py --run --target \"San José\"")
        print("\nTargets are hand-maintained in TARGETS.md.")
        sys.exit(0)


if __name__ == "__main__":
    main()
