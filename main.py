#!/usr/bin/env python3
# main.py - Housing List Aggregator Entry Point

import argparse
import sys
from datetime import datetime

def main():
    parser = argparse.ArgumentParser(description="Housing Waitlist Aggregator - Santa Clara County")
    parser.add_argument("--discover", action="store_true", help="First-run interactive discovery")
    parser.add_argument("--refresh-targets", action="store_true", help="Refresh targets")
    parser.add_argument("--run", action="store_true", help="Normal daily run")
    parser.add_argument("--review", action="store_true", help="Pause for human review before final output")
    args = parser.parse_args()

    if args.discover or args.refresh_targets:
        print("=== Housing List Aggregator Discovery ===")
        county = "Santa Clara County, California" if not args.discover else input("1. County or city?\n→ ").strip() or "Santa Clara County, California"
        seeds = []
        if args.discover:
            print("2. Starter seeds? (blank line to finish)")
            while True:
                line = input("→ ").strip()
                if line == "": break
                seeds.append(line)
        mode = input("\n3. A) Full auto-discovery or B) Seed-only? [A]\n→ ").strip().upper() or "A"
        from discovery import run_first_discovery
        run_first_discovery(county, seeds, mode)

    elif args.run:
        print(f"=== Normal Run Started at {datetime.now()} ===")
        
        from registry import load_targets_to_db
        load_targets_to_db()
        
        from normalizer import save_current_full
        from changelog import generate_changelog
        from outputs import generate_daily_summary
        
        all_listings = []
        
        print("\n🔄 Scraping all targets...")
        
        import sqlite3
        conn = sqlite3.connect("housing_registry.db")
        c = conn.cursor()
        c.execute("SELECT authority, url, scraping_measures FROM targets ORDER BY priority DESC")
        
        for authority, url, measures in c.fetchall():
            print(f"\n→ {authority}")
            try:
                if "playwright_needed" in measures or "js_heavy" in measures:
                    from playwright_scraper import playwright_scrape
                    all_listings.extend(playwright_scrape(authority, url))
                elif "sccha" in authority.lower() or "john stewart" in authority.lower():
                    from adapters.sccha import scrape_sccha
                    all_listings.extend(scrape_sccha(url))
                else:
                    from generic_scraper import generic_scrape
                    from scraper import polite_get
                    resp = polite_get(url)
                    if resp:
                        all_listings.extend(generic_scrape(authority, url, resp.text))
            except Exception as e:
                print(f"   Error on {authority}: {e}")
        
        conn.close()
        
        save_current_full(all_listings)
        generate_changelog([], all_listings)
        generate_daily_summary(all_listings)
        
        print(f"\n✅ Run complete! {len(all_listings)} listings.")
        print("   → current_full.csv + daily_summary.md ready")

    else:
        print("Usage: --discover | --refresh-targets | --run [--review]")

if __name__ == "__main__":
    main()
