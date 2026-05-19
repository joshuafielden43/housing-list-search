# main.py - Entry point for Housing List Aggregator

import argparse
from discovery import first_run_discovery
# TODO: implement full flow with registry, scraper, etc.

if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument('--discover', action='store_true')
    args = parser.parse_args()
    if args.discover:
        first_run_discovery()
    else:
        print("Normal run - TODO implement")