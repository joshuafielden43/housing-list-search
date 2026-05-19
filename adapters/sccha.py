# adapters/sccha.py
from bs4 import BeautifulSoup
import re

def scrape_sccha(url: str):
    print(f"🧩 Running SCCHA/John Stewart adapter on {url}")
    from scraper import polite_get
    resp = polite_get(url)
    if not resp:
        return []
    
    soup = BeautifulSoup(resp.text, 'html.parser')
    listings = []
    seen = set()

    for elem in soup.find_all(['h1','h2','h3','p','a','li','strong']):
        text = elem.get_text(strip=True)
        if len(text) < 20: 
            continue
        lower = text.lower()
        
        if not any(kw in lower for kw in ["waitlist", "interest list", "accepting", "lottery", "now open", "apply now", "open until"]):
            continue
            
        key = text[:100]
        if key in seen: 
            continue
        seen.add(key)

        deadline_match = re.search(r'(until|deadline|closes?)\s*[:\-]?\s*([A-Za-z0-9 ,]+202[0-9])', text, re.I)
        deadline = deadline_match.group(2) if deadline_match else ""

        listing = {
            "authority": "Santa Clara County Housing Authority (SCCHA)" if "sccha" in url else "John Stewart Company (SCCHA properties)",
            "property_name": text[:140].split('\n')[0].strip(),
            "url": url,
            "status": "Open" if any(x in lower for x in ["open", "accepting", "apply now", "now taking"]) else "Unknown",
            "deadline": deadline,
            "income_limits": "Low/Very Low Income (varies)",
            "unit_types": "Varies",
            "eligibility_flags": ["section_8", "low_income"],
            "notes": text[:500].replace('\n', ' ').strip(),
            "confidence": 0.85
        }
        listings.append(listing)
    
    print(f"   → Extracted {len(listings)} cleaned listings")
    return listings
