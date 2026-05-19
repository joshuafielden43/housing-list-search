#!/usr/bin/env python3
# main.py - Housing List Aggregator Entry Point

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
        
        from housing_list_search.registry import load_targets_to_db
        load_targets_to_db()
        
        from housing_list_search.normalizer import save_current_full
        from housing_list_search.changelog import generate_changelog
        from housing_list_search.outputs import generate_daily_summary
        
        all_listings = []
        
        print("\n🔄 Scraping all targets...")
        
        import sqlite3
        conn = sqlite3.connect("housing_registry.db")
        c = conn.cursor()
        c.execute("SELECT authority, url, scraping_measures FROM targets ORDER BY priority DESC")
        
        for authority, url, measures in c.fetchall():
            print(f"\n→ Processing: {authority}")
            try:
                if "housekeys" in authority.lower() or "housekeys" in url.lower():
                    from housing_list_search.adapters.housekeys import scrape_housekeys
                    all_listings.extend(scrape_housekeys(authority, url))
                elif "sccha" in authority.lower() or "john stewart" in authority.lower():
                    from housing_list_search.adapters.sccha import scrape_sccha
                    all_listings.extend(scrape_sccha(url))
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
        
        conn.close()
        
        save_current_full(all_listings)
        generate_changelog([], all_listings)
        generate_daily_summary(all_listings)
        
        print(f"\n✅ Run complete! {len(all_listings)} listings processed.")
        print("   Files ready: current_full.csv + daily_summary.md")

    else:
        print("Usage:")
        print("  python main.py --discover")
        print("  python main.py --refresh-targets")
        print("  python main.py --run")
        sys.exit(0)


if __name__ == "__main__":
    main()
