# main.py - Entry point for Housing List Aggregator (v0.6)

import argparse
from discovery import first_run_discovery

if __name__ == "__main__":
    parser = argparse.ArgumentParser(description='Housing Waitlist Aggregator')
    parser.add_argument('--discover', action='store_true', help='Run first-run interactive discovery')
    args = parser.parse_args()
    
    if args.discover:
        first_run_discovery()
    else:
        print('Normal run mode - full scraper coming next')
        # TODO: load registry, scrape, normalize, changelog, output to $OUTPUT_DIR