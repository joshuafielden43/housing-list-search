#!/usr/bin/env python3
"""
codex_pdf.py

Task-aware affordable/subsidized housing list scraper.

Example:
  ./codex_pdf.py \
    --seed "https://www.cityofgilroy.org/797/Affordable-Apartments" \
    --out gilroy_affordable_housing.csv \
    --audit gilroy_crawl_audit.csv \
    --max-child-pages 2 \
    --max-pdfs 3

Install:
  python3 -m pip install requests beautifulsoup4 lxml pdfplumber pymupdf playwright

Optional Playwright browser:
  python3 -m playwright install chromium
"""

from __future__ import annotations

import argparse
import csv
import io
import re
import sys
import time
from dataclasses import asdict, dataclass
from typing import Iterable, Optional
from urllib.parse import parse_qs, unquote, urljoin, urlparse

import requests
from bs4 import BeautifulSoup

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


# ----------------------------
# Link classification patterns
# ----------------------------

GOOD_LINK_TEXT = re.compile(
    r"""
    affordable|
    subsidized|
    low[\s-]?income|
    no[\s-]?income|
    senior|
    apartment|
    apartments|
    rental|
    rentals|
    housing\s+list|
    affordable\s+housing|
    income\s+restricted|
    facilities|
    availability|
    available|
    flyer|
    ami|
    below\s+market|
    bmr|
    wait[\s-]?list
    """,
    re.I | re.X,
)

BAD_LINK_TEXT = re.compile(
    r"""
    youtube|
    facebook|
    instagram|
    twitter|
    x\.com|
    linkedin|
    agenda|
    minutes|
    employment|
    jobs|
    parks|
    recreation|
    police|
    permit|
    permits|
    bill\s+pay|
    water|
    report\s+a\s+concern|
    site\s+map|
    privacy|
    copyright|
    terms|
    login|
    subscribe|
    search|
    newsletter|
    calendar|
    meeting|
    council
    """,
    re.I | re.X,
)

BAD_HOSTS = {
    "youtube.com",
    "www.youtube.com",
    "youtu.be",
    "facebook.com",
    "www.facebook.com",
    "instagram.com",
    "www.instagram.com",
    "x.com",
    "twitter.com",
    "linkedin.com",
    "www.linkedin.com",
}

DOCUMENT_HINTS = re.compile(
    r"""
    \.pdf($|\?)|
    /documentcenter/view/|
    docaccess\.com/docviewer|
    docviewer\.html|
    download
    """,
    re.I | re.X,
)


# ----------------------------
# Extraction patterns
# ----------------------------

PHONE_RE = re.compile(
    r"""
    (?:
      \(?\d{3}\)?[\s\.-]?
      \d{3}[\s\.-]?
      \d{4}
    )
    """,
    re.X,
)

EMAIL_RE = re.compile(
    r"[A-Z0-9._%+\-]+@[A-Z0-9.\-]+\.[A-Z]{2,}",
    re.I,
)

ADDRESS_RE = re.compile(
    r"""
    \b
    \d{1,6}
    \s+
    [A-Za-z0-9.'#\-\s]+?
    \s+
    (?:
      St\.?|Street|
      Ave\.?|Avenue|
      Rd\.?|Road|
      Dr\.?|Drive|
      Pl\.?|Place|
      Ct\.?|Court|
      Way|
      Blvd\.?|Boulevard|
      Ln\.?|Lane|
      Cir\.?|Circle|
      Pass|
      Pkwy\.?|Parkway|
      Hwy\.?|Highway|
      Terrace|
      Ter\.?
    )
    \b
    """,
    re.I | re.X,
)

BEDROOM_RE = re.compile(
    r"""
    (?:
      studio|
      studios|
      \d+\s*[-–]\s*\d+\s*bedroom|
      \d+\s*(?:,|&)\s*\d+\s*(?:&\s*\d+\s*)?bedroom|
      \d+\s*bedroom|
      \d+\s*br|
      \d+\s*[-–]\s*\d+\s*br
    )
    """,
    re.I | re.X,
)

COMMUNITY_TYPE_RE = re.compile(
    r"""
    senior\s*\d+\+|
    senior|
    family|
    general\s+public|
    agricultural\s+worker|
    farmworker|
    farm\s+worker|
    disabled|
    developmentally\s+disabled|
    unhoused\s+individual|
    homeless|
    permanent\s+supportive|
    supportive\s+housing|
    moderate\s+income|
    low\s+income|
    very\s+low\s+income
    """,
    re.I | re.X,
)


# ----------------------------
# Data rows
# ----------------------------

@dataclass
class CrawlDecision:
    seed_url: str
    source_page_url: str
    discovered_url: str
    normalized_url: str
    link_text: str
    decision: str
    score: int
    reason: str


@dataclass
class HousingRecord:
    seed_url: str
    source_page_url: str
    document_url: str
    wrapper_url: str
    extraction_method: str
    page_number: str
    raw_line: str
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


# ----------------------------
# HTTP / URL helpers
# ----------------------------

def fetch(url: str, timeout: int = 30) -> requests.Response:
    headers = {
        "User-Agent": (
            "Mozilla/5.0 housing-list-research-bot/0.1 "
            "(nonprofit affordable housing data extraction)"
        )
    }
    response = requests.get(
        url,
        headers=headers,
        timeout=timeout,
        allow_redirects=True,
    )
    response.raise_for_status()
    return response


def same_registered_domain_or_allowed(seed_url: str, candidate_url: str) -> bool:
    """
    Conservative default:
      - same host is allowed
      - subdomains of same host are allowed
      - docaccess.com is allowed only because we unwrap it
    """
    seed_host = urlparse(seed_url).hostname or ""
    candidate_host = urlparse(candidate_url).hostname or ""

    if not candidate_host:
        return False

    if candidate_host == seed_host:
        return True

    if candidate_host.endswith("." + seed_host):
        return True

    if candidate_host in {"docaccess.com", "www.docaccess.com"}:
        return True

    return False


def normalize_docaccess_url(url: str) -> tuple[str, str]:
    """
    If URL is a DocAccess docviewer wrapper and contains a real source URL,
    return (real_document_url, wrapper_url).

    Otherwise return (url, "").
    """
    parsed = urlparse(url)
    host = parsed.hostname or ""

    if host in {"docaccess.com", "www.docaccess.com"} and "docviewer" in parsed.path:
        query = parse_qs(parsed.query)
        wrapped = query.get("url", [""])[0]
        if wrapped:
            return unquote(wrapped), url

    return url, ""


def looks_like_pdf_url(url: str) -> bool:
    lowered = url.lower()
    return (
        ".pdf" in lowered
        or "/documentcenter/view/" in lowered
        or "/documentcenter/" in lowered
    )


# ----------------------------
# Link scoring
# ----------------------------

def score_link(
    seed_url: str,
    source_page_url: str,
    href: str,
    text: str,
) -> tuple[int, str]:
    absolute_url = urljoin(source_page_url, href or "")
    parsed = urlparse(absolute_url)
    host = parsed.hostname or ""

    if not href:
        return -100, "empty href"

    if parsed.scheme not in {"http", "https"}:
        return -100, f"unsupported scheme: {parsed.scheme}"

    if host in BAD_HOSTS:
        return -100, f"blocked host: {host}"

    if not same_registered_domain_or_allowed(seed_url, absolute_url):
        return -25, f"external host not allowed by default: {host}"

    haystack = f"{text} {absolute_url}"
    score = 0
    reasons: list[str] = []

    if GOOD_LINK_TEXT.search(haystack):
        score += 40
        reasons.append("housing keyword")

    if DOCUMENT_HINTS.search(absolute_url):
        score += 25
        reasons.append("document hint")

    if BAD_LINK_TEXT.search(haystack):
        score -= 60
        reasons.append("navigation/junk keyword")

    if "#" in absolute_url:
        score -= 5
        reasons.append("fragment")

    if re.search(r"/DocumentCenter/View/\d+", absolute_url, re.I):
        score += 20
        reasons.append("municipal document center PDF")

    if "docaccess.com/docviewer" in absolute_url.lower():
        real_url, _wrapper_url = normalize_docaccess_url(absolute_url)
        if real_url != absolute_url:
            score += 30
            reasons.append("docaccess wrapper with source URL")
        else:
            score -= 20
            reasons.append("docaccess wrapper without source URL")

    return score, "; ".join(reasons) or "no useful signal"


def discover_links(
    seed_url: str,
    html: str,
    source_page_url: str,
) -> tuple[list[str], list[CrawlDecision]]:
    soup = BeautifulSoup(html, "lxml")
    candidates: list[str] = []
    audit: list[CrawlDecision] = []

    for anchor in soup.find_all("a", href=True):
        href = anchor.get("href", "").strip()
        text = " ".join(anchor.get_text(" ", strip=True).split())

        absolute_url = urljoin(source_page_url, href)
        normalized_url, _wrapper_url = normalize_docaccess_url(absolute_url)
        score, reason = score_link(seed_url, source_page_url, href, text)

        if score >= 55:
            decision = "follow"
            candidates.append(normalized_url)
        elif score >= 25:
            decision = "maybe_skip"
        else:
            decision = "skip"

        audit.append(
            CrawlDecision(
                seed_url=seed_url,
                source_page_url=source_page_url,
                discovered_url=absolute_url,
                normalized_url=normalized_url,
                link_text=text,
                decision=decision,
                score=score,
                reason=reason,
            )
        )

    seen: set[str] = set()
    unique_candidates: list[str] = []

    for candidate in candidates:
        if candidate not in seen:
            seen.add(candidate)
            unique_candidates.append(candidate)

    return unique_candidates, audit


# ----------------------------
# HTML fetching
# ----------------------------

def fetch_html_with_requests(url: str) -> str:
    response = fetch(url)
    content_type = response.headers.get("content-type", "")

    if "pdf" in content_type.lower():
        raise ValueError(f"URL appears to be PDF, not HTML: {url}")

    return response.text


def fetch_html_with_playwright(url: str) -> str:
    from playwright.sync_api import sync_playwright

    with sync_playwright() as playwright:
        browser = playwright.chromium.launch(headless=True)
        page = browser.new_page()
        page.goto(url, wait_until="networkidle", timeout=45000)
        html = page.content()
        browser.close()

    return html


# ----------------------------
# PDF extraction
# ----------------------------

def extract_pdf_text_lines_with_pymupdf(data: bytes, pdf_url: str) -> list[tuple[int, str]]:
    """
    Fast PDF text extraction using PyMuPDF.

    This is the default path because it is much less likely than pdfplumber
    to hang on ugly municipal PDFs.
    """
    try:
        import fitz  # PyMuPDF
    except ImportError as exc:
        raise RuntimeError("PyMuPDF is not installed. Install with: pip install pymupdf") from exc

    lines: list[tuple[int, str]] = []

    with fitz.open(stream=data, filetype="pdf") as document:
        for page_index, page in enumerate(document, start=1):
            text = page.get_text("text") or ""
            for line in text.splitlines():
                clean = " ".join(line.strip().split())
                if clean:
                    lines.append((page_index, clean))

    if lines:
        print(
            f"PyMuPDF extracted {len(lines)} text lines from {pdf_url}",
            file=sys.stderr,
        )

    return lines


def extract_pdf_text_lines_with_pdfplumber(data: bytes, pdf_url: str) -> list[tuple[int, str]]:
    """
    Fallback PDF extraction using pdfplumber.

    pdfplumber can be better for table-ish PDFs, but it can also hang badly on
    weird PDF internals. That is why this is fallback only.
    """
    if pdfplumber is None:
        raise RuntimeError("pdfplumber is not installed")

    lines: list[tuple[int, str]] = []

    with pdfplumber.open(io.BytesIO(data)) as pdf:
        for page_index, page in enumerate(pdf.pages, start=1):
            print(
                f"pdfplumber extracting page {page_index} from {pdf_url}",
                file=sys.stderr,
            )
            text = page.extract_text(x_tolerance=2, y_tolerance=3) or ""

            for line in text.splitlines():
                clean = " ".join(line.strip().split())
                if clean:
                    lines.append((page_index, clean))

    if lines:
        print(
            f"pdfplumber extracted {len(lines)} text lines from {pdf_url}",
            file=sys.stderr,
        )

    return lines


def extract_pdf_text_lines(pdf_url: str) -> list[tuple[int, str]]:
    """
    Extract PDF text as (page_number, line).

    Order:
      1. PyMuPDF first.
      2. pdfplumber fallback.
    """
    response = fetch(pdf_url)
    data = response.content

    try:
        pymupdf_lines = extract_pdf_text_lines_with_pymupdf(data, pdf_url)
        if pymupdf_lines:
            return pymupdf_lines
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(
            f"PyMuPDF failed for {pdf_url}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )

    try:
        return extract_pdf_text_lines_with_pdfplumber(data, pdf_url)
    except KeyboardInterrupt:
        raise
    except Exception as exc:
        raise RuntimeError(
            f"PDF extraction failed for {pdf_url}: {type(exc).__name__}: {exc}"
        ) from exc


def clean_cell(value: object) -> str:
    if value is None:
        return ""
    return " ".join(str(value).replace("\n", " ").split()).strip()


def normalize_header(value: str) -> str:
    lowered = clean_cell(value).lower()
    lowered = re.sub(r"[^a-z0-9]+", "_", lowered)
    lowered = lowered.strip("_")
    return lowered


def header_to_field(header: str) -> str:
    h = normalize_header(header)

    if "apartment" in h or "complex" in h or "property" in h or "name" == h:
        return "property_name"
    if "address" in h:
        return "address"
    if "phone" in h or "telephone" in h:
        return "phone"
    if "email" in h or "e_mail" in h:
        return "email"
    if "manager" in h or "management" in h:
        return "property_manager"
    if "community" in h or "population" in h or "type" in h:
        return "community_type"
    if "bedroom" in h or "unit" in h or "br" == h:
        return "bedrooms"
    if "support" in h or "service" in h:
        return "supportive_services"

    return ""


def looks_like_header_row(row: list[str]) -> bool:
    joined = " ".join(row).lower()
    hits = 0
    for word in [
        "apartment",
        "complex",
        "property",
        "address",
        "phone",
        "manager",
        "community",
        "bedroom",
        "email",
        "supportive",
    ]:
        if word in joined:
            hits += 1
    return hits >= 2


def build_field_map(header_row: list[str]) -> dict[int, str]:
    field_map: dict[int, str] = {}
    for index, header in enumerate(header_row):
        field = header_to_field(header)
        if field:
            field_map[index] = field
    return field_map


def record_from_table_row(
    seed_url: str,
    source_page_url: str,
    document_url: str,
    wrapper_url: str,
    page_number: int,
    row: list[str],
    field_map: dict[int, str],
) -> Optional[HousingRecord]:
    values = {
        "property_name": "",
        "address": "",
        "phone": "",
        "email": "",
        "property_manager": "",
        "community_type": "",
        "bedrooms": "",
        "supportive_services": "",
    }

    for index, field in field_map.items():
        if index < len(row):
            values[field] = clean_cell(row[index])

    raw_line = " | ".join(clean_cell(cell) for cell in row if clean_cell(cell))

    if not raw_line:
        return None

    # Backfill obvious values in case headers were imperfect.
    if not values["phone"]:
        match = PHONE_RE.search(raw_line)
        if match:
            values["phone"] = match.group(0)

    if not values["email"]:
        match = EMAIL_RE.search(raw_line)
        if match:
            values["email"] = match.group(0)

    if not values["address"]:
        match = ADDRESS_RE.search(raw_line)
        if match:
            values["address"] = match.group(0)

    signals = sum(
        bool(values[key])
        for key in [
            "property_name",
            "address",
            "phone",
            "email",
            "property_manager",
            "community_type",
            "bedrooms",
        ]
    )

    if signals < 2:
        return None

    if signals >= 5:
        confidence = "high"
    elif signals >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    notes: list[str] = []
    if not values["property_name"]:
        notes.append("no property name detected")
    if not values["address"]:
        notes.append("no address detected")
    if not values["phone"]:
        notes.append("no phone detected")
    if not values["email"]:
        notes.append("no email detected")

    return HousingRecord(
        seed_url=seed_url,
        source_page_url=source_page_url,
        document_url=document_url,
        wrapper_url=wrapper_url,
        extraction_method="pdfplumber_table",
        page_number=str(page_number),
        raw_line=raw_line,
        property_name=values["property_name"],
        address=values["address"],
        phone=values["phone"],
        email=values["email"],
        property_manager=values["property_manager"],
        community_type=values["community_type"],
        bedrooms=values["bedrooms"],
        supportive_services=values["supportive_services"],
        confidence=confidence,
        notes="; ".join(notes),
    )


def extract_records_from_pdf_tables(
    seed_url: str,
    source_page_url: str,
    document_url: str,
    wrapper_url: str = "",
) -> list[HousingRecord]:
    if pdfplumber is None:
        return []

    response = fetch(document_url)
    data = response.content
    records: list[HousingRecord] = []

    table_settings = {
        "vertical_strategy": "lines",
        "horizontal_strategy": "lines",
        "intersection_tolerance": 8,
        "snap_tolerance": 4,
        "join_tolerance": 4,
        "edge_min_length": 3,
        "min_words_vertical": 2,
        "min_words_horizontal": 1,
        "text_tolerance": 3,
    }

    try:
        with pdfplumber.open(io.BytesIO(data)) as pdf:
            for page_number, page in enumerate(pdf.pages, start=1):
                tables = page.extract_tables(table_settings=table_settings) or []

                for table in tables:
                    cleaned_rows = [
                        [clean_cell(cell) for cell in row]
                        for row in table
                        if row and any(clean_cell(cell) for cell in row)
                    ]

                    if not cleaned_rows:
                        continue

                    header_index = None
                    for idx, row in enumerate(cleaned_rows[:5]):
                        if looks_like_header_row(row):
                            header_index = idx
                            break

                    if header_index is None:
                        continue

                    field_map = build_field_map(cleaned_rows[header_index])
                    if not field_map:
                        continue

                    for row in cleaned_rows[header_index + 1:]:
                        if looks_like_header_row(row):
                            continue

                        record = record_from_table_row(
                            seed_url=seed_url,
                            source_page_url=source_page_url,
                            document_url=document_url,
                            wrapper_url=wrapper_url,
                            page_number=page_number,
                            row=row,
                            field_map=field_map,
                        )

                        if record:
                            records.append(record)

    except KeyboardInterrupt:
        raise
    except Exception as exc:
        print(
            f"pdfplumber table extraction failed for {document_url}: {type(exc).__name__}: {exc}",
            file=sys.stderr,
        )
        return []

    if records:
        print(
            f"pdfplumber table extraction produced {len(records)} records from {document_url}",
            file=sys.stderr,
        )

    return records


# ----------------------------
# Record parsing
# ----------------------------

def parse_housing_line(
    seed_url: str,
    source_page_url: str,
    document_url: str,
    wrapper_url: str,
    page_number: int,
    line: str,
) -> Optional[HousingRecord]:
    """
    Heuristic row parser.

    It intentionally keeps raw_line, confidence and notes so a human can audit
    whether the row was parsed correctly.
    """
    lower = line.lower()

    if any(
        bad in lower
        for bad in [
            "apartment complex address phone",
            "apartment complex",
            "list of affordable rentals",
            "updated july",
            "property manager community type",
            "community type bedrooms",
            "supportive services",
            "city of gilroy",
            "planning division",
        ]
    ):
        return None

    phone_match = PHONE_RE.search(line)
    email_match = EMAIL_RE.search(line)
    address_match = ADDRESS_RE.search(line)
    bedroom_match = BEDROOM_RE.search(line)
    community_matches = list(COMMUNITY_TYPE_RE.finditer(line))

    if not (phone_match or email_match or address_match):
        return None

    address = address_match.group(0).strip() if address_match else ""
    phone = phone_match.group(0).strip() if phone_match else ""
    email = email_match.group(0).strip() if email_match else ""
    bedrooms = bedroom_match.group(0).strip() if bedroom_match else ""

    community_type = ""
    if community_matches:
        community_type = max(
            (match.group(0).strip() for match in community_matches),
            key=len,
        )

    property_name = ""
    property_manager = ""
    supportive_services = ""
    notes: list[str] = []

    if address_match:
        property_name = line[: address_match.start()].strip(" -–,")
    else:
        notes.append("no address detected")

    if phone_match:
        manager_start = phone_match.end()
        manager_end_candidates: list[int] = []

        if community_matches:
            manager_end_candidates.append(community_matches[0].start())

        if bedroom_match:
            manager_end_candidates.append(bedroom_match.start())

        if email_match:
            manager_end_candidates.append(email_match.start())

        manager_end_candidates = [
            candidate
            for candidate in manager_end_candidates
            if candidate > manager_start
        ]

        if manager_end_candidates:
            manager_end = min(manager_end_candidates)
            property_manager = line[manager_start:manager_end].strip(" -–,")
    else:
        notes.append("no phone detected")

    tail_start = None

    if email_match:
        tail_start = email_match.end()
    elif bedroom_match:
        tail_start = bedroom_match.end()
    elif community_matches:
        tail_start = community_matches[0].end()

    if tail_start is not None:
        tail = line[tail_start:].strip(" -–,")
        if tail and tail.upper() != "N/A":
            supportive_services = tail

    signals = sum(
        bool(value)
        for value in [
            property_name,
            address,
            phone,
            email,
            community_type,
            bedrooms,
        ]
    )

    if signals >= 5:
        confidence = "high"
    elif signals >= 3:
        confidence = "medium"
    else:
        confidence = "low"

    if not property_name:
        notes.append("no property name detected")
    if not email:
        notes.append("no email detected")
    if not bedrooms:
        notes.append("no bedroom field detected")
    if not community_type:
        notes.append("no community type detected")

    return HousingRecord(
        seed_url=seed_url,
        source_page_url=source_page_url,
        document_url=document_url,
        wrapper_url=wrapper_url,
        extraction_method="pymupdf_first_line_heuristic",
        page_number=str(page_number),
        raw_line=line,
        property_name=property_name,
        address=address,
        phone=phone,
        email=email,
        property_manager=property_manager,
        community_type=community_type,
        bedrooms=bedrooms,
        supportive_services=supportive_services,
        confidence=confidence,
        notes="; ".join(notes),
    )


def extract_records_from_pdf(
    seed_url: str,
    source_page_url: str,
    document_url: str,
    wrapper_url: str = "",
    include_low_confidence: bool = False,
) -> list[HousingRecord]:
    table_rows = extract_records_from_pdf_tables(
        seed_url=seed_url,
        source_page_url=source_page_url,
        document_url=document_url,
        wrapper_url=wrapper_url,
    )

    if table_rows:
        if include_low_confidence:
            return table_rows
        return [row for row in table_rows if row.confidence != "low"]

    rows: list[HousingRecord] = []
    lines = extract_pdf_text_lines(document_url)

    window: list[tuple[int, str]] = []

    def flush_window() -> Optional[HousingRecord]:
        nonlocal window
        if not window:
            return None

        page_number = window[0][0]
        combined = " ".join(part for _page, part in window)
        window = []

        record = parse_housing_line(
            seed_url=seed_url,
            source_page_url=source_page_url,
            document_url=document_url,
            wrapper_url=wrapper_url,
            page_number=page_number,
            line=combined,
        )

        if not record:
            return None

        if not include_low_confidence and record.confidence == "low":
            return None

        return record

    for page_number, line in lines:
        has_address = bool(ADDRESS_RE.search(line))
        has_phone = bool(PHONE_RE.search(line))
        has_email = bool(EMAIL_RE.search(line))

        if has_address and window:
            record = flush_window()
            if record:
                rows.append(record)

        if has_address or has_phone or has_email or window:
            window.append((page_number, line))

        if window:
            combined = " ".join(part for _page, part in window)
            if ADDRESS_RE.search(combined) and PHONE_RE.search(combined) and EMAIL_RE.search(combined):
                record = flush_window()
                if record:
                    rows.append(record)

    record = flush_window()
    if record:
        rows.append(record)

    return rows


# ----------------------------
# Scrape flow
# ----------------------------

def add_max_pdf_audit_row(
    audit: list[CrawlDecision],
    seed_url: str,
    source_page_url: str,
    discovered_url: str,
    normalized_url: str,
    max_pdfs: int,
) -> None:
    audit.append(
        CrawlDecision(
            seed_url=seed_url,
            source_page_url=source_page_url,
            discovered_url=discovered_url,
            normalized_url=normalized_url,
            link_text="",
            decision="skip",
            score=0,
            reason=f"max PDF limit reached: {max_pdfs}",
        )
    )


def scrape_seed(
    seed_url: str,
    use_playwright: bool = False,
    max_child_pages: int = 10,
    max_pdfs: int = 5,
    include_low_confidence: bool = False,
) -> tuple[list[HousingRecord], list[CrawlDecision]]:
    if use_playwright:
        html = fetch_html_with_playwright(seed_url)
    else:
        html = fetch_html_with_requests(seed_url)

    candidate_urls, audit = discover_links(seed_url, html, seed_url)

    records: list[HousingRecord] = []
    child_pages_seen = 0
    pdfs_processed = 0

    for candidate_url in candidate_urls:
        real_url, wrapper_url = normalize_docaccess_url(candidate_url)

        try:
            if looks_like_pdf_url(real_url):
                if pdfs_processed >= max_pdfs:
                    add_max_pdf_audit_row(
                        audit= audit,
                        seed_url=seed_url,
                        source_page_url=seed_url,
                        discovered_url=candidate_url,
                        normalized_url=real_url,
                        max_pdfs=max_pdfs,
                    )
                    continue

                pdfs_processed += 1

                records.extend(
                    extract_records_from_pdf(
                        seed_url=seed_url,
                        source_page_url=seed_url,
                        document_url=real_url,
                        wrapper_url=wrapper_url,
                        include_low_confidence=include_low_confidence,
                    )
                )

                time.sleep(0.5)
                continue

            if child_pages_seen >= max_child_pages:
                audit.append(
                    CrawlDecision(
                        seed_url=seed_url,
                        source_page_url=seed_url,
                        discovered_url=candidate_url,
                        normalized_url=real_url,
                        link_text="",
                        decision="skip",
                        score=0,
                        reason=f"max child page limit reached: {max_child_pages}",
                    )
                )
                continue

            child_pages_seen += 1

            try:
                child_html = fetch_html_with_requests(real_url)
            except Exception as exc:
                audit.append(
                    CrawlDecision(
                        seed_url=seed_url,
                        source_page_url=seed_url,
                        discovered_url=candidate_url,
                        normalized_url=real_url,
                        link_text="",
                        decision="error",
                        score=0,
                        reason=f"child HTML fetch failed: {type(exc).__name__}: {exc}",
                    )
                )
                continue

            child_candidates, child_audit = discover_links(
                seed_url=seed_url,
                html=child_html,
                source_page_url=real_url,
            )
            audit.extend(child_audit)

            for child_url in child_candidates:
                child_real_url, child_wrapper_url = normalize_docaccess_url(child_url)

                if not looks_like_pdf_url(child_real_url):
                    continue

                if pdfs_processed >= max_pdfs:
                    add_max_pdf_audit_row(
                        audit=audit,
                        seed_url=seed_url,
                        source_page_url=real_url,
                        discovered_url=child_url,
                        normalized_url=child_real_url,
                        max_pdfs=max_pdfs,
                    )
                    continue

                pdfs_processed += 1

                try:
                    records.extend(
                        extract_records_from_pdf(
                            seed_url=seed_url,
                            source_page_url=real_url,
                            document_url=child_real_url,
                            wrapper_url=child_wrapper_url,
                            include_low_confidence=include_low_confidence,
                        )
                    )
                except Exception as exc:
                    audit.append(
                        CrawlDecision(
                            seed_url=seed_url,
                            source_page_url=real_url,
                            discovered_url=child_url,
                            normalized_url=child_real_url,
                            link_text="",
                            decision="error",
                            score=0,
                            reason=f"child PDF extraction failed: {type(exc).__name__}: {exc}",
                        )
                    )

                time.sleep(0.5)

        except KeyboardInterrupt:
            raise
        except Exception as exc:
            audit.append(
                CrawlDecision(
                    seed_url=seed_url,
                    source_page_url=seed_url,
                    discovered_url=candidate_url,
                    normalized_url=real_url,
                    link_text="",
                    decision="error",
                    score=0,
                    reason=f"{type(exc).__name__}: {exc}",
                )
            )

    return records, audit


# ----------------------------
# CSV output
# ----------------------------

def write_csv(path: str, rows: Iterable[object], fieldnames: list[str]) -> None:
    with open(path, "w", newline="", encoding="utf-8") as file_handle:
        writer = csv.DictWriter(file_handle, fieldnames=fieldnames)
        writer.writeheader()

        for row in rows:
            writer.writerow(asdict(row))


def write_records_csv(path: str, rows: Iterable[HousingRecord]) -> None:
    write_csv(
        path,
        rows,
        [
            "seed_url",
            "source_page_url",
            "document_url",
            "wrapper_url",
            "extraction_method",
            "page_number",
            "raw_line",
            "property_name",
            "address",
            "phone",
            "email",
            "property_manager",
            "community_type",
            "bedrooms",
            "supportive_services",
            "confidence",
            "notes",
        ],
    )


def write_audit_csv(path: str, rows: Iterable[CrawlDecision]) -> None:
    write_csv(
        path,
        rows,
        [
            "seed_url",
            "source_page_url",
            "discovered_url",
            "normalized_url",
            "link_text",
            "decision",
            "score",
            "reason",
        ],
    )


# ----------------------------
# CLI
# ----------------------------

def main() -> int:
    parser = argparse.ArgumentParser(
        description="Extract affordable/subsidized housing list data from a seed page."
    )
    parser.add_argument(
        "--seed",
        required=True,
        help="Seed affordable housing page URL",
    )
    parser.add_argument(
        "--out",
        default="housing_records.csv",
        help="Output CSV for extracted records",
    )
    parser.add_argument(
        "--audit",
        default="crawl_audit.csv",
        help="Output CSV for crawl/link decisions",
    )
    parser.add_argument(
        "--use-playwright",
        action="store_true",
        help="Render the seed page with Playwright instead of requests",
    )
    parser.add_argument(
        "--max-child-pages",
        type=int,
        default=10,
        help="Maximum number of non-PDF child pages to inspect",
    )
    parser.add_argument(
        "--max-pdfs",
        type=int,
        default=5,
        help="Maximum number of PDFs to extract before stopping",
    )
    parser.add_argument(
        "--include-low-confidence",
        action="store_true",
        help="Include low-confidence fragment rows in the output CSV",
    )

    args = parser.parse_args()

    records, audit = scrape_seed(
        seed_url=args.seed,
        use_playwright=args.use_playwright,
        max_child_pages=args.max_child_pages,
        max_pdfs=args.max_pdfs,
        include_low_confidence=args.include_low_confidence,
    )

    write_records_csv(args.out, records)
    write_audit_csv(args.audit, audit)

    print(f"Wrote {len(records)} extracted record rows to {args.out}")
    print(f"Wrote {len(audit)} crawl audit rows to {args.audit}")

    return 0


if __name__ == "__main__":
    raise SystemExit(main())
