#!/usr/bin/env python3
# cli.py - Housing List Aggregator CLI (importable from the package)

import argparse
import sys
from datetime import datetime

def main():
    parser = argparse.ArgumentParser(description="Housing Waitlist Aggregator")
    parser.add_argument("--discover", action="store_true", help="Interactive first-run discovery")
    parser.add_argument("--refresh-targets", action="store_true", help="Reset TARGETS.md to defaults (destructive — requires --yes-i-know)")
    parser.add_argument("--yes-i-know", action="store_true", help="Confirm destructive --refresh-targets overwrite")
    parser.add_argument("--run", action="store_true", help="Normal daily scrape")
    parser.add_argument(
        "--target",
        metavar="AUTHORITY",
        help="With --run: process only targets whose authority contains this substring (case-insensitive)",
    )
    args = parser.parse_args()

    if args.discover or args.refresh_targets:
        print("=== Housing List Aggregator Discovery ===")

        if args.discover:
            county = input("1. What county or city are we targeting?\n→ ").strip() or "Santa Clara County, California"
            print("\n2. Any starter seed URLs? (blank line to finish)")
            seeds = []
            while True:
                line = input("→ ").strip()
                if line == "":
                    break
                seeds.append(line)
        else:
            county = "Santa Clara County, California"
            seeds = []

        mode = input("\n3. A) Full auto-discovery or B) Seed-only? [default A]\n→ ").strip().upper() or "A"

        from housing_list_search.discovery import run_first_discovery
        run_first_discovery(county, seeds, mode, confirmed=getattr(args, "yes_i_know", False))

    elif args.run:
        print(f"=== Normal Run Started at {datetime.now()} ===")

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

        # Log intentionally skipped targets (no_public_list)
        skipped_targets = []
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

        # Export CSV outputs from DB
        n_full = db.export_csv("current_full.csv")
        n_diff = db.export_diff_csv("diff.csv", run_id=run_id)
        print(f"   Exported current_full.csv ({n_full} rows), diff.csv ({n_diff} rows)")

        diff_counts = db.diff_counts(run_id)
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

        generate_changelog(all_listings, skipped_targets=skipped_targets)
        generate_daily_summary(all_listings, skipped_targets=skipped_targets)

        print(f"\n✅ Run complete! {len(all_listings)} listings this run "
              f"({counts['inserted']} new, {counts['updated']} updated).")
        if skipped_targets:
            print(f"   ⚠️  Skipped {len(skipped_targets)} targets marked no_public_list")
        print("   Files: current_full.csv  diff.csv  daily_summary.md  changelog_diffs.md")

    else:
        print("Usage:")
        print("  python main.py --discover")
        print("  python main.py --refresh-targets")
        print("  python main.py --run")
        print("  python main.py --run --target \"San José\"")
        sys.exit(0)


if __name__ == "__main__":
    main()
