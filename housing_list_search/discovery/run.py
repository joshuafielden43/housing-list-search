"""
Discovery entry point — used by cli.py --discover / --refresh-targets.

NOTE: run_first_discovery() does NOT overwrite TARGETS.md when the file
already contains curated data (i.e. has more than the header rows). The
--discover flow is for first-time bootstrap of a new county only.
"""

import os
from datetime import datetime


def run_first_discovery(county: str, seed_urls: list, mode: str, confirmed: bool = False):
    """
    confirmed: must be True when called from --refresh-targets to overwrite curated data.
               Prevents accidental destruction of the hand-edited TARGETS.md.
               Set via the --yes-i-know flag in cli.py.
    """
    targets_path = "TARGETS.md"

    # Guard: refuse to overwrite a non-trivial TARGETS.md unless the caller
    # explicitly passed confirmed=True (i.e. used --yes-i-know on the CLI).
    if os.path.exists(targets_path):
        with open(targets_path, encoding="utf-8") as f:
            existing = f.read()
        if existing.count("\n") > 15 and not confirmed:
            print(
                "\n⚠️  TARGETS.md already exists and appears to contain curated data.\n"
                "   --discover is for first-time bootstrap only.\n"
                "   Edit TARGETS.md directly to add or modify targets.\n"
                "\n"
                "   To reset to defaults (DESTRUCTIVE — overwrites your curated data):\n"
                "       python main.py --refresh-targets --yes-i-know\n"
            )
            return

    print(f"\nRunning discovery for {county} in {mode.upper()} mode...\n")

    targets = []

    targets.extend([
        {
            "authority": "Santa Clara County Housing Authority (SCCHA)",
            "url": "https://www.scchousingauthority.org/",
            "notes": "Main Section 8 / Interest Lists + property portals.",
            "scraping_measures": "playwright_needed, robots_respect",
            "priority": "High"
        },
        {
            "authority": "John Stewart Company (SCCHA properties)",
            "url": "https://jscosccha.com/",
            "notes": "Property-specific waitlists and lotteries",
            "scraping_measures": "native_requests, table_based",
            "priority": "High"
        },
        {
            "authority": "City of San José Affordable Housing Portal",
            "url": "https://housing.sanjoseca.gov/listings",
            "notes": "Bloom Housing platform — SSR, use native_requests",
            "scraping_measures": "native_requests",
            "priority": "High"
        },
    ])

    if mode.upper() == "A":
        city_data = {
            "Campbell": "https://housing-group.org/campbell/",
            "Cupertino": "https://www.cupertino.gov/Your-City/Departments/Community-Development/Housing/BMR-Program-Overview",
            "Gilroy": "https://www.cityofgilroy.org/279/Housing-and-Community-Services",
            "Los Altos": "https://housing-group.org/losaltos/",
            "Los Gatos": "https://www.losgatosca.gov/345/Housing-Programs",
            "Milpitas": "https://www.milpitas.gov/1303/Below-Market-Rate-BMR-Homeownership-Prog",
            "Morgan Hill": "https://www.morganhill.ca.gov/629/Housing",
            "Mountain View": "https://www.housekeys13.com/",
            "Palo Alto": "https://www.paloalto.gov/Departments/Planning-Development-Services/Housing-Policies-Projects/Below-Market-Rate-Housing",
            "Santa Clara": "https://housingbayarea.mtc.ca.gov/listings",
            "Sunnyvale": "https://www.sunnyvale.ca.gov/homes-streets-and-property/housing/rental-programs",
            "Menlo Park": "https://housing-group.org/menlopark/",
            "Half Moon Bay": "https://housing-group.org/halfmoonbay/",
        }
        for city, url in city_data.items():
            targets.append({
                "authority": f"City of {city}",
                "url": url,
                "notes": "Verify current waitlist status. See TARGETS.md for known measures.",
                "scraping_measures": "native_requests",
                "priority": "Medium"
            })

    _write_targets_md(county, targets, seed_urls, targets_path)
    print(f"\nDiscovery complete — {len(targets)} targets written to {targets_path}.")
    print("Review and edit TARGETS.md, then run: python main.py --run")


def _write_targets_md(county: str, targets: list, seed_urls: list, path: str):
    timestamp = datetime.now().strftime("%Y-%m-%d")
    header = (
        f"# TARGETS.md – Housing Waitlist Targets\n"
        f"County: {county}\n"
        f"Last Discovery Run: {timestamp}\n"
        f"Scope: full (human review required)\n\n"
        f"City/Authority | URL | Notes | Scraping Measures | Priority | Last Seen"
        f" | Administrator | Administrator URL | Administrator Phone | Administrator Contact\n"
        f"---|---|---|---|---|---|---|---|---|---\n"
    )
    rows = "".join(
        f"{t['authority']} | {t['url']} | {t['notes']} | {t['scraping_measures']}"
        f" | {t['priority']} | {timestamp} |  |  |  | \n"
        for t in targets
    )
    seeds = (
        "\n## User-Provided Seeds\n" + "\n".join(f"- {u}" for u in seed_urls) + "\n"
        if seed_urls else ""
    )
    with open(path, "w", encoding="utf-8") as f:
        f.write(header + rows + seeds)
