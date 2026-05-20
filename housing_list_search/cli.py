#!/usr/bin/env python3
# cli.py - Housing List Aggregator CLI (importable from the package)

import argparse
import sys
from datetime import datetime

def main():
    parser = argparse.ArgumentParser(description="Housing Waitlist Aggregator")
    parser.add_argument("--discover", action="store_true", help="Interactive first-run discovery")
    parser.add_argument("--refresh-targets", action="store_true", help="Heavyweight discovery + proposals")
    parser.add_argument("--run", action="store_true", help="Normal daily scrape")
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
        run_first_discovery(county, seeds, mode)

    elif args.run:
        print(f"=== Normal Run Started at {datetime.now()} ===")
        
        import logging
        import sys
        logging.basicConfig(
            level=logging.INFO,
            format="%(asctime)s [%(levelname)s] %(message)s",
            handlers=[logging.StreamHandler(sys.stdout)]
        )
        logger = logging.getLogger("housing_list_search")

        from housing_list_search.registry import load_targets_to_db, get_active_targets, get_skipped_targets
        load_targets_to_db()
        
        from housing_list_search.normalizer import save_current_full
        from housing_list_search.changelog import generate_changelog
        from housing_list_search.outputs import generate_daily_summary
        
        all_listings = []
        
        # Get the intentionally skipped targets first (for reporting)
        skipped_rows = get_skipped_targets()
        skipped_targets = []
        for t in skipped_rows:
            authority = t["authority"]
            notes = t.get("notes") or ""
            logger.warning(
                f"SKIPPING {authority} — marked 'no_public_list' in TARGETS.md. "
                "Documented to prevent repeated research. See Notes column for details. "
                "Remove the marker when a public structured source appears."
            )
            skipped_targets.append((authority, notes[:200]))

        # Now get only the targets we should actually process
        active_targets = get_active_targets()
        
        print("\n🔄 Scraping all targets...")
        
        for t in active_targets:
            authority = t["authority"]
            url = t["url"]
            measures = t.get("scraping_measures") or ""
            notes = t.get("notes") or ""
            admin = t.get("administrator") or ""
            admin_url = t.get("administrator_url") or ""
            admin_phone = t.get("administrator_phone") or ""
            admin_contact = t.get("administrator_contact") or ""

            print(f"\n→ Processing: {authority}")
            try:
                # NEW high-quality extraction path (preferred)
                from housing_list_search.extraction import extract_target
                new_records = extract_target(url, authority)
                if new_records:
                    print(f"   [extraction] {len(new_records)} structured records via new path")
                    for r in new_records:
                        all_listings.append(r.to_dict())
                    continue

                # First-class adapters
                # Only attempt GIS extraction for targets that explicitly declare "gis" in measures (prevents spam on delegated-admin targets like HouseKeys, Housing Group, Alta)
                if admin and ("gis" in (measures or "")):
                    from housing_list_search.adapters.gis_extraction import extract_gis_portfolio
                    recs = extract_gis_portfolio(
                        url,
                        authority,
                        administrator=admin,
                        administrator_url=admin_url,
                        administrator_phone=admin_phone,
                        administrator_contact=admin_contact,
                    )
                    if recs:
                        print(f"   [gis_extraction] {len(recs)} records (administrator: {admin})")
                        all_listings.extend(recs)
                        continue

                if "properties-list" in url.lower() and "scchousingauthority.org" in url.lower():
                    from housing_list_search.adapters.john_stewart import scrape_john_stewart
                    all_listings.extend(scrape_john_stewart(url))
                elif "john stewart" in authority.lower() or "jscosccha" in url.lower():
                    from housing_list_search.adapters.john_stewart import scrape_john_stewart
                    all_listings.extend(scrape_john_stewart(url))
                elif "housekeys" in authority.lower() or "housekeys" in url.lower():
                    from housing_list_search.adapters.housekeys import scrape_housekeys
                    all_listings.extend(scrape_housekeys(authority, url))
                elif "cdn" in measures:
                    from housing_list_search.adapters.cdn import extract_underlying_records
                    recs = extract_underlying_records(url, authority)
                    all_listings.extend(recs)
                elif "alta" in measures:
                    from housing_list_search.adapters.alta import scrape_alta
                    all_listings.extend(scrape_alta(authority, url))
                elif "playwright_needed" in measures or "js_heavy" in measures:
                    from housing_list_search.playwright_scraper import playwright_scrape
                    all_listings.extend(playwright_scrape(authority, url))
                else:
                    from housing_list_search.generic_scraper import generic_scrape
                    from housing_list_search.scraper import polite_get
                    resp = polite_get(url)
                    if resp:
                        all_listings.extend(generic_scrape(authority, url, resp.text))
            except Exception as e:
                print(f"   Error on {authority}: {e}")
        
        # Deduplicate across sources (San José portal + SCCHA directory + others will overlap)
        from housing_list_search.dedupe import deduplicate_listings
        all_listings = deduplicate_listings(all_listings)

        save_current_full(all_listings)
        generate_changelog([], all_listings, skipped_targets=skipped_targets)
        generate_daily_summary(all_listings, skipped_targets=skipped_targets)
        
        print(f"\n✅ Run complete! {len(all_listings)} listings processed.")
        if skipped_targets:
            print(f"   ⚠️  Skipped {len(skipped_targets)} targets marked no_public_list (see daily_summary.md and logs)")
        print("   Files ready: current_full.csv + daily_summary.md")

    else:
        print("Usage:")
        print("  python main.py --discover")
        print("  python main.py --refresh-targets")
        print("  python main.py --run")
        sys.exit(0)


if __name__ == "__main__":
    main()
