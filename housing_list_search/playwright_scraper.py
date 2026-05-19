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
            junk_prefixes = r'^(skip to main content|home|your city|departments|community development|housing and community services|quick links|calendar|municipal code|get social|jobs|contact us|records|english|select this as).*?\|?\s*'

            for elem in soup.find_all(['h1','h2','h3','p','a','li','button','div']):
                text = elem.get_text(" ", strip=True)
                if len(text) < 35:
                    continue
                lower = text.lower()

                if not any(kw in lower for kw in keywords):
                    continue

                clean_name = re.sub(junk_prefixes, '', text, flags=re.I)
                clean_name = re.sub(r'\s+', ' ', clean_name).strip()[:105]

                if len(clean_name) < 20 or clean_name.lower().startswith(("quick links", "skip to", "home /", "your city /")):
                    continue

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
                    "notes": text[:360].replace('\n', ' ').strip(),
                    "confidence": 0.7
                }
                listings.append(listing)

            browser.close()
    except Exception as e:
        print(f"   Playwright error: {e}")

    print(f"   → Playwright found {len(listings)} cleaned listings")
    return listings
