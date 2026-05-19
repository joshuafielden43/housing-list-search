# adapters/housekeys.py
from bs4 import BeautifulSoup
import re
from urllib.parse import urljoin

def is_housekeys_url(url: str) -> bool:
    return "housekeys" in url.lower()


def scrape_housekeys(authority: str, url: str):
    print(f"🧩 Running HouseKeys adapter on {url}")
    listings = []
    seen = set()

    from housing_list_search.scraper import polite_get
    from pdf_scraper import extract_from_pdf

    # Get main page
    resp = polite_get(url)
    if not resp:
        return []

    soup = BeautifulSoup(resp.text, 'html.parser')

    # Find relevant sub-pages and PDF flyers
    pdf_links = []
    sub_links = []

    for a in soup.find_all('a', href=True):
        href = a['href'].lower()
        text = a.get_text(" ", strip=True).lower()
        full_url = urljoin(url, a['href'])

        if href.endswith('.pdf') or 'documentcenter/view' in href:
            pdf_links.append(full_url)
        elif any(kw in text for kw in ["affordable apartment", "available units", "bmr", "rental", "opportunity"]):
            if full_url not in sub_links:
                sub_links.append(full_url)

    # Scrape HTML sub-pages
    pages = [(url, resp.text)] + [(link, None) for link in sub_links[:8]]

    for page_url, page_html in pages:
        if page_html is None:
            resp = polite_get(page_url)
            if not resp:
                continue
            page_html = resp.text

        page_soup = BeautifulSoup(page_html, 'html.parser')

        for elem in page_soup.find_all(['h1','h2','h3','p','li','div']):
            text = elem.get_text(" ", strip=True)
            if len(text) < 40:
                continue
            lower = text.lower()

            if not any(kw in lower for kw in ["available units", "available unit", "now open", "apply now", "lottery"]):
                continue

            clean_name = re.sub(r'\s+', ' ', text).strip()[:120]

            key = clean_name.lower()[:60]
            if key in seen:
                continue
            seen.add(key)

            deadline_match = re.search(r'(until|deadline|closes?)\s*[:\-]?\s*([A-Za-z0-9 ,]+202[0-9])', text, re.I)
            deadline = deadline_match.group(2) if deadline_match else ""

            listing = {
                "authority": authority,
                "property_name": clean_name,
                "url": page_url,
                "status": "Open",
                "deadline": deadline,
                "income_limits": "Low/Very Low (varies)",
                "unit_types": "Varies",
                "eligibility_flags": ["low_income"],
                "notes": text[:450].replace('\n', ' ').strip(),
                "confidence": 0.85
            }
            listings.append(listing)

    # Extract from PDF flyers (the real gold)
    for pdf_url in pdf_links[:12]:
        pdf_listings = extract_from_pdf(pdf_url, authority)
        listings.extend(pdf_listings)

    print(f"   → HouseKeys adapter found {len(listings)} listings")
    return listings
