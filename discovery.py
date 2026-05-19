# discovery.py - Interactive first-run discovery

def first_run_discovery():
    print("=== Housing List Aggregator First-Run Discovery ===")
    county = input("1. What county or city are we targeting? (e.g. 'Santa Clara County, California')\n→ ")
    seeds = input("2. Any starter seed URLs? (one per line or blank)\n→ ").strip()
    mode = input("3. A) Full auto-discovery or B) Seed-only? (A/B)\n→ ").upper()
    print(f"\nDiscovery for {county} in {mode} mode. TODO: implement full logic and TARGETS.md update.")
    # TODO: implement city list gen, search, propose diff
