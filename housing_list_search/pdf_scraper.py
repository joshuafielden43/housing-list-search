import requests
import pdfplumber
import re
import io
import fitz  # PyMuPDF
from dataclasses import dataclass
from typing import List, Tuple, Optional
from urllib.parse import urlparse, parse_qs, unquote

@dataclass
class HousingRecord:
    authority: str
    property_name: str
    address: str
    phone: str
    email: str
    property_manager: str
    community_type: str
    bedrooms: str
    supportive_services: str
    confidence: str
    notes: str
    document_url: str

def fetch(url: str) -> requests.Response:
    headers = {"User-Agent": "Mozilla/5.0 (housing-list-research-bot/0.1 nonprofit affordable housing data extraction)"}
    resp = requests.get(url, headers=headers, timeout=30, allow_redirects=True)
    resp.raise_for_status()
    return resp

def normalize_docaccess_url(url: str) -> tuple[str, str]:
    parsed = urlparse(url)
    if parsed.hostname in {"docaccess.com", "www.docaccess.com"} and "docviewer" in parsed.path:
        query = parse_qs(parsed.query)
        wrapped = query.get("url", [""])[0]
        if wrapped:
            return unquote(wrapped), url
    return url, ""

def extract_from_pdf(pdf_url: str, authority: str) -> List[HousingRecord]:
    print(f"   → Extracting PDF: {pdf_url}")
    listings = []
    try:
        real_url, wrapper_url = normalize_docaccess_url(pdf_url)
        resp = fetch(real_url)
        data = resp.content

        with fitz.open(stream=data, filetype="pdf") as doc:
            for page_idx, page in enumerate(doc, start=1):
                text = page.get_text("text") or ""
                # New: try whole-page extraction for common Gilroy / Housing Group flyer style first
                page_records = _extract_flyer_page(authority, real_url, wrapper_url, page_idx, text)
                if page_records:
                    listings.extend(page_records)
                    continue

                # Fallback: old line-by-line heuristic
                for line in text.splitlines():
                    clean = " ".join(line.strip().split())
                    if clean:
                        record = parse_housing_line(authority, real_url, wrapper_url, page_idx, clean)
                        if record:
                            listings.append(record)

    except Exception as e:
        print(f"   PDF extraction error: {e}")

    print(f"   → PDF scraper found {len(listings)} listings")
    return listings

def _extract_flyer_page(authority: str, document_url: str, wrapper_url: str, page_number: int, full_text: str) -> List[HousingRecord]:
    """Strong extractor for common single-page Gilroy / Eden Housing style flyers.

    Typical layout:
        Wheeler Manor
        Apartments
        651 W. 6th St
        Gilroy, CA 95020
        (408) 847-5490
        ...
        Available Now!!!
        1 Bedroom - 1 Bath
        Rent $1,822.00 ...
        Maximum Annual Income
        1 Person $ 84,420
        ...
        Eden Housing
    """
    t = full_text
    t_lower = t.lower()
    records = []

    if "apartments" not in t_lower and "manor" not in t_lower:
        return []

    # Property name - handle split lines + fallback to URL slug
    property_name = ""
    m = re.search(r'([A-Z][A-Za-z][A-Za-z ]{2,30}?)\s*\n\s*Apartments', t)
    if m:
        property_name = (m.group(1) + " Apartments").strip()
    else:
        m = re.search(r'^([A-Z][A-Za-z][A-Za-z ]{4,40})', t)
        if m:
            property_name = m.group(1).strip()

    # If still weak, derive from the document_url slug (very common on Gilroy)
    if len(property_name) < 5 or property_name.lower() in ("apartments", "manor"):
        slug = ""
        if "/DocumentCenter/View/" in document_url:
            m = re.search(r"/DocumentCenter/View/\d+/([A-Za-z0-9_-]+)", document_url)
            if m:
                slug = m.group(1)
        if slug:
            slug = slug.replace("-", " ").replace("_", " ")
            slug = re.sub(r'\s*(Flyer|Event|Calendar|50AMI|60AMI|50%|60%)\s*', ' ', slug, flags=re.I).strip()
            if len(slug) > 4:
                property_name = slug

    # Address
    address = ""
    m = re.search(r'(\d+[^,\n]{4,40}St\.?|Ave\.?)\s*\n\s*([A-Za-z ,]+CA\s*\d{5})', t)
    if m:
        address = (m.group(1) + ", " + m.group(2)).strip()
    else:
        m = re.search(r'(\d+[^,\n]+,\s*CA\s*\d{5})', t)
        if m:
            address = m.group(1).strip()

    # Phone
    phone = ""
    m = re.search(r'\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}', t)
    if m:
        phone = m.group(0)

    # AMI from filename
    ami = ""
    if "50ami" in document_url.lower():
        ami = "50% AMI"
    elif "60ami" in document_url.lower():
        ami = "60% AMI"

    # Unit + rent (handles "1 Bedroom - 1 Bath" + "Rent $1,822.00" and variations)
    bedrooms = ""
    rent = ""
    m = re.search(r'(\d+)\s*(?:Bedroom|BR)[^$]*?Rent\s*\$?\s*([\d,]+)', t, re.IGNORECASE)
    if m:
        bedrooms = f"{m.group(1)} Bedroom"
        rent = m.group(2).replace(",", "")

    # Also look for "Available Now" or "X units available" language
    available = ""
    if re.search(r'available\s*now|available!!!', t, re.I):
        available = "Available Now"
    elif re.search(r'(\d+)\s*(?:unit|units)\s*(?:available|avail)', t, re.I):
        available = "Some units available"

    # Manager
    manager = "Eden Housing" if "eden housing" in t_lower else ""

    # Income limits
    income = {}
    for m in re.finditer(r'(\d+)\s*(Person|People)\s*\$?\s*([\d,]+)', t, re.IGNORECASE):
        income[m.group(1) + " Person"] = m.group(3).replace(",", "")

    # Notes
    notes = []
    if "62 or older" in t_lower:
        notes.append("Age restricted (62+)")
    if "pet friendly" in t_lower:
        notes.append("Pet friendly")
    if ami:
        notes.append(ami)
    if rent:
        notes.append(f"Rent ${rent}")
    if available:
        notes.append(available)
    if income:
        notes.append("Income: " + ", ".join(f"{k}: ${v}" for k, v in income.items()))

    rec = HousingRecord(
        authority=authority,
        property_name=property_name or "Unknown Property",
        address=address,
        phone=phone,
        email="",
        property_manager=manager,
        community_type="Senior" if "senior" in t_lower or "62" in t_lower else "",
        bedrooms=bedrooms,
        supportive_services="",
        confidence="medium",
        notes="; ".join(notes),
        document_url=document_url,
    )
    records.append(rec)
    return records


def parse_housing_line(authority: str, document_url: str, wrapper_url: str, page_number: int, line: str) -> Optional[HousingRecord]:
    """Line-by-line fallback parser (kept for compatibility with other document styles)."""
    l = line.lower()

    # Very basic signals — expand as we see more flyer styles
    if "available" in l and ("unit" in l or "now" in l):
        return HousingRecord(
            authority=authority,
            property_name="",
            address="",
            phone="",
            email="",
            property_manager="",
            community_type="",
            bedrooms="",
            supportive_services="",
            confidence="low",
            notes=line[:200],
            document_url=document_url,
        )
    return None
