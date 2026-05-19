# playwright_scraper.py
from bs4 import BeautifulSoup
import re

def playwright_scrape(authority: str, url: str):
    print(f"   → Using Playwright for dynamic site: {url}")
    listings = []
    try:
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            browser = p.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(url, wait_until="domcontentloaded", timeout=60000)
            page.wait_for_timeout(8000)
            
            content = page.content()
            soup = BeautifulSoup(content, 'html.parser')
            
            keywords = ["waitlist", "interest list", "accepting applications", "lottery", "now open", "apply now", "bmr", "below market"]
            
            for elem in soup.find_all(['h1','h2','h3','p','a','li','button','div']):
                text = elem.get_text(strip=True)
                if len(text) < 25: continue
                lower = text.lower()
                
                if not any(kw in lower for kw in keywords): continue
                
                clean_name = re.sub(r'^[^A-Za-z0-9]+', '', text[:160]).strip()
                clean_name = re.sub(r'\s+', ' ', clean_name)[:110]
                
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
                    "confidence": 0.75
                }
                listings.append(listing)
            
            browser.close()
    except Exception as e:
        print(f"   Playwright error: {e}")
    
    print(f"   → Playwright found {len(listings)} cleaned listings")
    return listings
