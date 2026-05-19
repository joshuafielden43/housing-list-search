# discovery.py
import os
from datetime import datetime

def run_first_discovery(county: str, seed_urls: list, mode: str):
    print(f"\n🔍 Running discovery for {county} in {mode.upper()} mode...\n")
    
    targets = []
    
    # === Always include core regional authorities ===
    targets.extend([
        {
            "authority": "Santa Clara County Housing Authority (SCCHA)",
            "url": "https://www.scchousingauthority.org/",
            "notes": "Main Section 8 / Interest Lists + property portals. Portal: https://portal.scchousingauthority.org",
            "scraping_measures": "playwright_needed, robots_respect, delay_5s",
            "priority": "High"
        },
        {
            "authority": "John Stewart Company (SCCHA properties)",
            "url": "https://jscosccha.com/",
            "notes": "Property-specific waitlists and lotteries",
            "scraping_measures": "native_requests, table_based, robots_respect",
            "priority": "High"
        },
        {
            "authority": "City of San José Affordable Housing Portal",
            "url": "https://housing.sanjoseca.gov/",
            "notes": "Current accepting applications + map",
            "scraping_measures": "playwright_needed, js_heavy, robots_respect",
            "priority": "High"
        }
    ])
    
    if mode.upper() == "A":  # Full auto-discovery
        print("🔎 Performing full auto-discovery across Santa Clara County cities...")
        
        city_data = {
            "Campbell": "https://www.campbellca.gov/635/Below-Market-Rate-Program",
            "Cupertino": "https://www.cupertino.gov/Your-City/Departments/Community-Development/Housing/BMR-Program-Overview",
            "Gilroy": "https://www.cityofgilroy.org/279/Housing-and-Community-Services",
            "Los Altos": "https://www.losaltosca.gov/212/Affordable-Housing",
            "Los Gatos": "https://www.losgatosca.gov/345/Housing-Programs",
            "Milpitas": "https://www.milpitasca.gov/",
            "Morgan Hill": "https://www.morganhill.ca.gov/629/Housing",
            "Mountain View": "https://www.mountainview.gov/our-city/departments/housing/affordable-housing-536",
            "Palo Alto": "https://www.cityofpaloalto.org/Departments/Community-Services/Housing",
            "Santa Clara": "https://www.santaclaraca.gov/services/housing-community-services",
            "Sunnyvale": "https://www.sunnyvale.ca.gov/homes-streets-and-property/housing/rental-programs",
            # Smaller cities
            "Los Altos Hills": "https://www.losaltoshillsca.gov/",
            "Monte Sereno": "https://www.monteserenoca.gov/",
            "Saratoga": "https://www.saratoga.ca.us/",
        }
        
        for city, url in city_data.items():
            targets.append({
                "authority": f"City of {city}",
                "url": url,
                "notes": "BMR, lotteries, or SCCHA referrals. Verify current waitlist status.",
                "scraping_measures": "native_requests, check_for_pdfs, robots_respect",
                "priority": "Medium"
            })
    
    # Write TARGETS.md
    write_targets_md(county, targets, seed_urls)
    
    print(f"\n✅ Discovery complete! {len(targets)} targets written.")
    print("Review/edit TARGETS.md, then run `python main.py --run`")

def write_targets_md(county: str, targets: list, seed_urls: list):
    timestamp = datetime.now().strftime("%Y-%m-%d")
    content = f"""# TARGETS.md – Housing Waitlist Targets
County: {county}
Last Discovery Run: {timestamp}
Scope: full (human review required)
Notes: Deduplicate SCCHA overlaps. Edit freely.

City/Authority | URL | Notes | Scraping Measures | Priority | Last Seen
---|---|---|---|---|---
"""
    
    for t in targets:
        content += f"{t['authority']} | {t['url']} | {t['notes']} | {t['scraping_measures']} | {t['priority']} | {timestamp}\n"
    
    if seed_urls:
        content += "\n## User-Provided Seeds\n" + "\n".join([f"- {u}" for u in seed_urls]) + "\n"
    
    with open("TARGETS.md", "w", encoding="utf-8") as f:
        f.write(content)
