# generic_scraper.py
import re
from urllib.parse import urljoin, urlparse

from bs4 import BeautifulSoup


def generic_scrape(authority: str, url: str, html: str):
    soup = BeautifulSoup(html, "html.parser")
    listings = []
    seen = set()

    pdf_links = []
    sub_links = []

    for a in soup.find_all("a", href=True):
        href = a["href"]
        text = a.get_text(" ", strip=True).lower()
        full_url = urljoin(url, href)

        if href.lower().endswith(".pdf") or "documentcenter/view" in href.lower():
            pdf_links.append(full_url)
        elif any(kw in text for kw in ["affordable apartment", "available units", "bmr", "rental"]):
            if full_url not in sub_links and full_url != url:
                p = urlparse(full_url)
                base = urlparse(url).netloc.lower()
                if p.netloc == base or (p.netloc and p.netloc.endswith("." + base)):
                    sub_links.append(full_url)

    # Scrape HTML pages
    pages = [(url, html, authority)] + [(link, None, authority) for link in sub_links[:6]]

    for page_url, page_html, page_authority in pages:
        if page_html is None:
            from housing_list_search.scraper import polite_get

            resp = polite_get(page_url)
            if not resp:
                continue
            page_html = resp.text

        page_soup = BeautifulSoup(page_html, "html.parser")

        for elem in page_soup.find_all(["h1", "h2", "h3", "p", "a", "li", "strong", "div"]):
            text = elem.get_text(" ", strip=True)
            if len(text) < 40:
                continue
            lower = text.lower()

            if not any(
                kw in lower
                for kw in ["available units", "available unit", "now open", "apply now", "lottery"]
            ):
                continue

            clean_name = re.sub(r"\s+", " ", text).strip()[:110]

            key = (clean_name.lower()[:55], page_authority)
            if key in seen:
                continue
            seen.add(key)

            deadline_match = re.search(
                r"(until|deadline|closes?)\s*[:\-]?\s*([A-Za-z0-9 ,]+202[0-9])", text, re.I
            )
            deadline = deadline_match.group(2) if deadline_match else ""

            listing = {
                "authority": page_authority,
                "property_name": clean_name,
                "url": page_url,
                "status": "Open",
                "deadline": deadline,
                "income_limits": "Low/Very Low (varies)",
                "unit_types": "Varies",
                "eligibility_flags": ["low_income"],
                "notes": text[:400].replace("\n", " ").strip(),
                "confidence": 0.75,
            }
            listings.append(listing)

    # Extract from PDF flyers
    from housing_list_search.extraction.pdf import extract_records_from_pdf

    for pdf_url in pdf_links[:12]:
        for rec in extract_records_from_pdf(pdf_url, authority):
            listings.append(rec.to_dict())

    print(f"   → Generic scraper found {len(listings)} cleaned listings (including PDFs)")
    return listings
