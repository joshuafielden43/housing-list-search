# generic_scraper.py
from bs4 import BeautifulSoup
import re

def generic_scrape(authority: str, url: str, html: str):
    soup = BeautifulSoup(html, 'html.parser')
    listings = []
    seen = set()

    keywords = ["waitlist", "interest list", "accepting applications", "lottery", "now open", "apply now", "bmr", "below market", "affordable"]

    for elem in soup.find_all(['h1','h2','h3','p','a','li','strong','div']):
        text = elem.get_text(strip=True)
        if len(text) < 25: 
            continue
        lower = text.lower()
        
        if not any(kw in lower for kw in keywords): 
            continue
            
        # Clean title aggressively
        clean_name = re.sub(r'^[^A-Za-z0-9]+', '', text[:160]).strip()
        clean_name = re.sub(r'\s+', ' ', clean_name)[:110]
        
        key = (clean_name.lower(), authority)
        if key in seen: 
            continue
        seen.add(key)

        deadline_match = re.search(r'(until|deadline|closes?)\s*[:\-]?\s*([A-Za-z0-9 ,]+202[0-9])', text, re.I)
        deadline = deadline_match.group(2) if deadline_match else ""

        listing = {
            "authority": authority,
            "property_name": clean_name,
            "url": url,
            "status": "Open" if any(x in lower for x in ["open", "accepting", "apply now", "lottery"]) else "Unknown",
            "deadline": deadline,
            "income_limits": "Low/Very Low (varies)",
            "unit_types": "Varies",
            "eligibility_flags": ["low_income"],
            "notes": text[:400].replace('\n', ' ').strip(),
            "confidence": 0.65
        }
        listings.append(listing)
    
    print(f"   → Generic scraper found {len(listings)} cleaned listings")
    return listings
