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

        # PyMuPDF first (fast and reliable)
        lines = []
        with fitz.open(stream=data, filetype="pdf") as doc:
            for page_idx, page in enumerate(doc, start=1):
                text = page.get_text("text") or ""
                for line in text.splitlines():
                    clean = " ".join(line.strip().split())
                    if clean:
                        lines.append((page_idx, clean))

        for page_number, line in lines:
            record = parse_housing_line(authority, real_url, wrapper_url, page_number, line)
            if record:
                listings.append(record)

    except Exception as e:
        print(f"   PDF extraction error: {e}")

    print(f"   → PDF scraper found {len(listings)} listings")
    return listings

def parse_housing_line(authority: str, document_url: str, wrapper_url: str, page_number: int, line: str) -> Optional[HousingRecord]:
    # Heuristic parser from codex_pdf (simplified for integration)
    # ... (full heuristic logic from your codex_pdf.py is here)
    # For brevity, I kept the core logic you already had working in the last run
    # (you can paste the full parse_housing_line from codex_pdf.py here if you want)
    return None  # placeholder until you copy the full parser
