"""
PDF Extraction Module

This is the integration of the working extraction logic originally developed
in codex_pdf.py. The goal is high-quality, structured extraction from
affordable housing PDFs (especially the kind of lists Gilroy publishes).

We keep the conservative, high-signal philosophy:
- Prefer precision over recall.
- Output structured records that can feed the CSV pipeline.
- Designed so an LLM (when used later) can help with scoring or cleaning,
  but never makes the final extraction decision by itself.
"""

from __future__ import annotations

import io
import logging
import re
from dataclasses import dataclass
from urllib.parse import parse_qs, unquote, urlparse

logger = logging.getLogger(__name__)

try:
    import pdfplumber
except ImportError:
    pdfplumber = None


@dataclass
class HousingRecord:
    """Structured record for one affordable housing opportunity / property."""

    authority: str = ""
    property_name: str = ""
    address: str = ""
    phone: str = ""
    email: str = ""
    property_manager: str = ""
    community_type: str = ""
    occupancy_type: str = ""
    bedrooms: str = ""
    supportive_services: str = ""
    notes: str = ""
    document_url: str = ""
    # Normalised availability status: "open", "closed", "waitlist", "coming_soon", or ""
    # Set by structured extractors (Bloom, etc.); empty for generic/PDF records.
    listing_status: str = ""
    page_number: int = 0
    raw_line: str = ""
    confidence: str = "low"  # "high", "medium", or "low"

    # Freshness / delta metadata (added 2026-05 for 0.8.2+)
    last_seen: str = ""  # ISO timestamp when last observed in a source
    first_seen: str = ""  # ISO timestamp when first seen
    source: str = ""  # e.g. "cdn:sunnyvale:370" or "housekeys:los-gatos"
    source_url: str = ""  # canonical URL of the document/listing this came from
    expires_at: str = ""  # optional explicit expiry if the source provides one

    def to_dict(self) -> dict:
        """Convert to plain dict for downstream CSV/outputs."""
        return {
            "authority": self.authority,
            "property_name": self.property_name,
            "address": self.address,
            "phone": self.phone,
            "email": self.email,
            "property_manager": self.property_manager,
            "community_type": self.community_type,
            "occupancy_type": self.occupancy_type,
            "bedrooms": self.bedrooms,
            "supportive_services": self.supportive_services,
            "notes": self.notes,
            "document_url": self.document_url,
            "url": self.document_url,  # alias expected by some older code
            "listing_status": self.listing_status,
            "confidence": self.confidence,
            "page_number": self.page_number,
            # Freshness fields
            "last_seen": self.last_seen,
            "first_seen": self.first_seen,
            "source": self.source,
            "source_url": self.source_url,
            "expires_at": self.expires_at,
        }


# ------------------------------------------------------------------
# PDF Text Extraction
# ------------------------------------------------------------------


def _looks_like_pdf(content: bytes, *, content_type: str = "", url: str = "") -> bool:
    """True if body starts with PDF magic or Content-Type/url strongly indicate PDF (#791)."""
    if content[:5] == b"%PDF-":
        return True
    # Some servers prepend a BOM or whitespace
    stripped = content.lstrip()
    if stripped[:5] == b"%PDF-":
        return True
    ct = (content_type or "").lower()
    if "pdf" in ct and content[:1] != b"<":  # not HTML
        return True
    return False


def _fetch_pdf(url: str, timeout: int = 30) -> bytes:
    """Download a PDF via polite_get (robots.txt + rate limit). Handles DocumentCenter redirects."""
    from housing_list_search.access import polite_get, validate_http_url

    # Re-validate final URL (incl. docaccess-unwrapped targets already checked in normalize)
    try:
        validate_http_url(url)
    except Exception as exc:
        raise ValueError(f"PDF URL failed policy: {url}: {exc}") from exc

    resp = polite_get(url)
    if not resp:
        raise ValueError(f"Could not fetch PDF (robots.txt disallow or HTTP failure): {url}")

    content = resp.content
    content_type = resp.headers.get("content-type", "")
    if not _looks_like_pdf(content, content_type=content_type, url=url):
        # DocumentCenter may return HTML wrapper — do not feed HTML to pdfplumber
        head = content[:80].decode("utf-8", errors="replace").replace("\n", " ")
        raise ValueError(
            f"Response is not a PDF for {url} (content-type={content_type!r}, head={head!r})"
        )

    return content


def _iter_pdf_page_text(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """Extract (page_number, page_text) via pdfplumber."""
    if pdfplumber is None or not pdf_bytes:
        return []
    pages: list[tuple[int, str]] = []
    try:
        # Must be a file-like object — raw bytes as stream= fails silently.
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
            for page_idx, page in enumerate(pdf.pages, start=1):
                pages.append((page_idx, page.extract_text() or ""))
    except Exception as exc:
        logger.debug("[pdf] page text extract failed: %s", exc)
        return []
    return pages


def extract_text_lines_from_pdf(pdf_bytes: bytes) -> list[tuple[int, str]]:
    """Extract text from a PDF as (page_number, line) tuples (pdfplumber)."""
    lines: list[tuple[int, str]] = []
    for page_idx, text in _iter_pdf_page_text(pdf_bytes):
        for line in text.splitlines():
            clean = " ".join(line.strip().split())
            if clean:
                lines.append((page_idx, clean))

    if not lines and pdfplumber is None:
        raise RuntimeError("No PDF text extraction library available (need pdfplumber)")

    return lines


# ------------------------------------------------------------------
# Heuristic Parsing (ported + adapted from codex_pdf.py)
# ------------------------------------------------------------------

# Common regexes for parsing property lines
PHONE_RE = re.compile(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}")
EMAIL_RE = re.compile(r"[\w\.-]+@[\w\.-]+\.\w+")
ADDRESS_RE = re.compile(
    r"\d{1,5}\s+[\w\s\.\,\-]+(?:Ave|St|Street|Rd|Road|Dr|Drive|Blvd|Boulevard|Way|Ln|Lane|Ct|Court|Pl|Place|Cir|Circle)\b",
    re.I,
)
BEDROOM_RE = re.compile(r"\d[\s,-]*(?:bed|bdrm|bedroom)s?", re.I)

COMMUNITY_TYPE_RE = re.compile(
    r"\b(?:senior|family|general public|disabled|unhoused|homeless|"
    r"veteran|agricultural worker|farmworker|moderate income|low income|"
    r"very low income|workforce)\b",
    re.I,
)


# ------------------------------------------------------------------
# Table Extraction Helpers (ported from codex_pdf.py)
# ------------------------------------------------------------------


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
    raw = str(header).lower()

    # More specific matches first
    if any(k in h for k in ["manager", "management"]):
        return "property_manager"
    if any(k in h for k in ["apartment", "complex", "property name"]):
        return "property_name"
    if "address" in h:
        return "address"
    if any(k in h for k in ["phone", "telephone"]):
        return "phone"
    if any(k in h for k in ["email", "e_mail"]):
        return "email"
    if "occupancy" in h or "occupancy" in raw:
        return "occupancy_type"
    if any(k in h for k in ["community", "population", "type"]):
        return "community_type"
    if any(k in h for k in ["bedroom", "unit", "br"]):
        return "bedrooms"
    if any(k in h for k in ["support", "service"]):
        return "supportive_services"
    return ""


def looks_like_header_row(row: list[str]) -> bool:
    joined = " ".join(row).lower()
    hits = sum(
        1
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
        ]
        if word in joined
    )
    return hits >= 2


def build_field_map(header_row: list[str]) -> dict[int, str]:
    field_map: dict[int, str] = {}
    for index, header in enumerate(header_row):
        field = header_to_field(header)
        if field:
            field_map[index] = field
    return field_map


def record_from_table_row(
    row: list[str],
    field_map: dict[int, str],
    document_url: str = "",
    page_number: int = 0,
) -> HousingRecord | None:
    values = {
        "property_name": "",
        "address": "",
        "phone": "",
        "email": "",
        "property_manager": "",
        "community_type": "",
        "occupancy_type": "",
        "bedrooms": "",
        "supportive_services": "",
    }

    for index, field in field_map.items():
        if index < len(row):
            values[field] = clean_cell(row[index])

    raw_line = " | ".join(clean_cell(cell) for cell in row if clean_cell(cell))

    if not raw_line:
        return None

    # Backfill using regexes if headers were imperfect
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
        bool(values[k])
        for k in [
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
        document_url=document_url,
        page_number=page_number,
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


def parse_housing_line(
    line: str, document_url: str = "", page_number: int = 0
) -> HousingRecord | None:
    """
    Heuristic parser that turns a raw line from a housing list PDF into a structured record.
    This is the core logic that made the original Gilroy extraction work.
    """
    if not line or len(line) < 15:
        return None

    lower = line.lower()

    # Skip obvious header / junk lines
    junk_markers = [
        "apartment complex address phone",
        "list of affordable rentals",
        "updated july",
        "property manager community type",
        "community type bedrooms",
        "supportive services",
        "city of gilroy",
    ]
    if any(marker in lower for marker in junk_markers):
        return None

    phone_match = PHONE_RE.search(line)
    email_match = EMAIL_RE.search(line)
    address_match = ADDRESS_RE.search(line)
    bedroom_match = BEDROOM_RE.search(line)
    community_matches = list(COMMUNITY_TYPE_RE.finditer(line))

    # We require at least one strong signal
    if not (phone_match or email_match or address_match):
        return None

    record = HousingRecord(
        document_url=document_url,
        page_number=page_number,
        raw_line=line,
    )

    if address_match:
        record.address = address_match.group(0).strip()
        # Property name is usually everything before the address
        record.property_name = line[: address_match.start()].strip(" -–,")

    if phone_match:
        record.phone = phone_match.group(0).strip()

    if email_match:
        record.email = email_match.group(0).strip()

    if bedroom_match:
        record.bedrooms = bedroom_match.group(0).strip()

    if community_matches:
        # Take the longest match as the community type
        record.community_type = max((m.group(0).strip() for m in community_matches), key=len)

    # Simple confidence heuristic
    signals = sum(
        bool(x)
        for x in [
            record.address,
            record.phone,
            record.email,
            record.bedrooms,
            record.community_type,
        ]
    )
    if signals >= 4:
        record.confidence = "high"
    elif signals >= 2:
        record.confidence = "medium"
    else:
        record.confidence = "low"

    return record


def normalize_docaccess_url(url: str) -> tuple[str, str]:
    """Unwrap docaccess.com viewer URLs; returns (real_url, wrapper_url)."""
    from housing_list_search.access import URLPolicyError, validate_http_url

    parsed = urlparse(url)
    if parsed.hostname in {"docaccess.com", "www.docaccess.com"} and "docviewer" in parsed.path:
        query = parse_qs(parsed.query)
        wrapped = query.get("url", [""])[0]
        if wrapped:
            unwrapped = unquote(wrapped)
            try:
                return validate_http_url(unwrapped), url
            except URLPolicyError as exc:
                raise ValueError(f"docaccess wrapper target failed URL policy: {exc}") from exc
    try:
        return validate_http_url(url), ""
    except URLPolicyError as exc:
        raise ValueError(f"PDF URL failed URL policy: {exc}") from exc


def _extract_flyer_page(
    authority: str,
    document_url: str,
    page_number: int,
    full_text: str,
) -> list[HousingRecord]:
    """Extractor for single-page Gilroy / Eden Housing style flyers."""
    t = full_text
    t_lower = t.lower()
    records: list[HousingRecord] = []

    # Multi-property BMR contact cards (Los Altos) must not collapse to one flyer row.
    if _looks_like_bmr_contact_directory(t):
        return []

    if "apartments" not in t_lower and "manor" not in t_lower:
        return []

    property_name = ""
    m = re.search(r"([A-Z][A-Za-z][A-Za-z ]{2,30}?)\s*\n\s*Apartments", t)
    if m:
        property_name = (m.group(1) + " Apartments").strip()
    else:
        m = re.search(r"^([A-Z][A-Za-z][A-Za-z ]{4,40})", t)
        if m:
            property_name = m.group(1).strip()

    if len(property_name) < 5 or property_name.lower() in ("apartments", "manor"):
        slug = ""
        if "/DocumentCenter/View/" in document_url:
            m = re.search(r"/DocumentCenter/View/\d+/([A-Za-z0-9_-]+)", document_url)
            if m:
                slug = m.group(1)
        if slug:
            slug = slug.replace("-", " ").replace("_", " ")
            slug = re.sub(
                r"\s*(Flyer|Event|Calendar|50AMI|60AMI|50%|60%)\s*", " ", slug, flags=re.I
            ).strip()
            if len(slug) > 4:
                property_name = slug

    address = ""
    m = re.search(r"(\d+[^,\n]{4,40}St\.?|Ave\.?)\s*\n\s*([A-Za-z ,]+CA\s*\d{5})", t)
    if m:
        address = (m.group(1) + ", " + m.group(2)).strip()
    else:
        m = re.search(r"(\d+[^,\n]+,\s*CA\s*\d{5})", t)
        if m:
            address = m.group(1).strip()

    phone = ""
    m = re.search(r"\(?\d{3}\)?[\s.-]?\d{3}[\s.-]?\d{4}", t)
    if m:
        phone = m.group(0)

    ami = ""
    if "50ami" in document_url.lower():
        ami = "50% AMI"
    elif "60ami" in document_url.lower():
        ami = "60% AMI"

    bedrooms = ""
    rent = ""
    m = re.search(r"(\d+)\s*(?:Bedroom|BR)[^$]*?Rent\s*\$?\s*([\d,]+)", t, re.IGNORECASE)
    if m:
        bedrooms = f"{m.group(1)} Bedroom"
        rent = m.group(2).replace(",", "")

    available = ""
    if re.search(r"available\s*now|available!!!", t, re.I):
        available = "Available Now"
    elif re.search(r"(\d+)\s*(?:unit|units)\s*(?:available|avail)", t, re.I):
        available = "Some units available"

    manager = "Eden Housing" if "eden housing" in t_lower else ""

    income: dict[str, str] = {}
    for m in re.finditer(r"(\d+)\s*(Person|People)\s*\$?\s*([\d,]+)", t, re.IGNORECASE):
        income[m.group(1) + " Person"] = m.group(3).replace(",", "")

    notes: list[str] = []
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

    records.append(
        HousingRecord(
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
            page_number=page_number,
        )
    )
    return records


def _extract_flyer_pages_from_pdf(
    pdf_bytes: bytes,
    authority: str,
    document_url: str,
) -> list[HousingRecord]:
    """Try whole-page flyer extraction for each page (pdfplumber)."""
    records: list[HousingRecord] = []
    try:
        for page_idx, text in _iter_pdf_page_text(pdf_bytes):
            page_records = _extract_flyer_page(authority, document_url, page_idx, text)
            if page_records:
                records.extend(page_records)
    except Exception as exc:
        logger.warning("[pdf] Flyer extraction failed for %s: %s", document_url, exc)
    return records


# Full street + city + CA ZIP (Los Altos BMR rental contact cards, two-column PDF text).
_CA_ADDR_RE = re.compile(
    r"\d{1,5}\s+[A-Za-z0-9 .#'/-]+?"
    r"(?:Ave(?:nue)?|St(?:reet)?|Rd|Road|Dr(?:ive)?|Blvd|Boulevard|Way|Ln|Lane|"
    r"Ct|Court|Pl(?:ace)?|Cir(?:cle)?|Real)\.?"
    r",?\s*[A-Za-z .]+?,?\s*CA\s*\d{5}",
    re.I,
)
_COMING_SOON_RE = re.compile(
    r"COMING\s+SOON:\s*([^,\n]+?),\s*(\d{1,5}\s+.+?CA\s*\d{5})",
    re.I,
)


def _looks_like_bmr_contact_directory(text: str) -> bool:
    """True for multi-property BMR contact flyers (e.g. Los Altos View/2785)."""
    lower = (text or "").lower()
    if "bmr" not in lower and "below market" not in lower:
        return False
    if "wait list" not in lower and "waitlist" not in lower:
        # still allow if many CA addresses (directory without waitlist wording)
        if len(_CA_ADDR_RE.findall(text or "")) < 2:
            return False
    return len(_CA_ADDR_RE.findall(text or "")) >= 2


def _split_side_by_side_names(name_line: str, n: int) -> list[str]:
    """Split a two-column name line into n property titles."""
    name_line = (name_line or "").strip()
    if n <= 1:
        return [name_line] if name_line else [""]
    words = name_line.split()
    if not words:
        return [""] * n
    # "The Terraces 569 Lassen Street" — second column starts with a digit
    digit_idx = next((i for i, w in enumerate(words) if w[:1].isdigit()), None)
    if n == 2 and digit_idx is not None and digit_idx > 0:
        return [" ".join(words[:digit_idx]), " ".join(words[digit_idx:])]
    if n == 2 and len(words) >= 4:
        # "Los Altos Gardens Fremont Avenue" → first 3 words | rest
        if words[0] in {"Los", "San", "The", "El", "La"}:
            return [" ".join(words[:3]), " ".join(words[3:])]
        return [" ".join(words[:2]), " ".join(words[2:])]
    # Even split of words as last resort
    chunk = max(1, len(words) // n)
    out: list[str] = []
    for i in range(n):
        start = i * chunk
        end = len(words) if i == n - 1 else (i + 1) * chunk
        out.append(" ".join(words[start:end]))
    return out


def extract_bmr_contact_directory(
    text: str,
    *,
    authority: str = "",
    document_url: str = "",
    page_number: int = 1,
) -> list[HousingRecord]:
    """
    Parse multi-property BMR rental contact cards from prose/two-column PDF text.

    Los Altos DocumentCenter View/2785 layout: intro + paired name/address/phone
    lines (columns merged by pdfplumber) + optional COMING SOON line.
    These are waitlist/contact directory rows — not live unit counts.
    """
    if not text or not _looks_like_bmr_contact_directory(text):
        return []

    records: list[HousingRecord] = []
    coming = list(_COMING_SOON_RE.finditer(text))
    # Strip coming-soon spans so address pairing ignores them
    work = text
    for m in reversed(coming):
        work = work[: m.start()] + work[m.end() :]

    addrs = list(_CA_ADDR_RE.finditer(work))
    phones = list(PHONE_RE.finditer(work))
    emails = list(EMAIL_RE.finditer(work))

    if len(addrs) < 2:
        return []

    # Group consecutive addresses that share a line (two-column row).
    # Map each address to the non-empty line immediately above the first address
    # of its row, then split names for that row.
    i = 0
    phone_i = 0
    email_i = 0
    while i < len(addrs):
        row_addrs = [addrs[i]]
        # Same line index = side-by-side pair
        line_no = work[: addrs[i].start()].count("\n")
        j = i + 1
        while j < len(addrs) and work[: addrs[j].start()].count("\n") == line_no:
            row_addrs.append(addrs[j])
            j += 1

        # Name line: last non-empty line before this address row
        prefix = work[: row_addrs[0].start()]
        name_candidates = [ln.strip() for ln in prefix.splitlines() if ln.strip()]
        name_line = name_candidates[-1] if name_candidates else ""
        # Skip boilerplate name lines
        if name_line.lower().startswith("please contact") or "wait list" in name_line.lower():
            name_line = name_candidates[-2] if len(name_candidates) >= 2 else ""

        names = _split_side_by_side_names(name_line, len(row_addrs))
        for k, am in enumerate(row_addrs):
            name = (names[k] if k < len(names) else "").strip() or "Unknown Property"
            # Drop program headers mistaken as names
            if name.upper().startswith("LOS ALTOS BMR") or "PROGRAM" in name.upper() and "GARDEN" not in name.upper():
                if "Gardens" not in name and "Project" not in name:
                    name = "Unknown Property"
            phone = phones[phone_i].group(0) if phone_i < len(phones) else ""
            phone_i += 1
            email = ""
            # Prefer email whose position is after this address and before next row
            if email_i < len(emails):
                em = emails[email_i]
                next_start = (
                    row_addrs[k + 1].start()
                    if k + 1 < len(row_addrs)
                    else (addrs[j].start() if j < len(addrs) else len(work))
                )
                if am.start() < em.start() < next_start + 120:
                    # pdfplumber sometimes prefixes www. on email-like tokens
                    email = re.sub(r"^www\.", "", em.group(0), flags=re.I)
                    email_i += 1
            records.append(
                HousingRecord(
                    authority=authority,
                    property_name=name,
                    address=am.group(0).strip(),
                    phone=phone,
                    email=email,
                    confidence="medium",
                    listing_status="waitlist",
                    notes="BMR rental contact directory; waitlist via on-site property manager",
                    document_url=document_url,
                    source_url=document_url,
                    page_number=page_number,
                    raw_line=name_line,
                )
            )
        i = j

    for m in coming:
        records.append(
            HousingRecord(
                authority=authority,
                property_name=m.group(1).strip(),
                address=m.group(2).strip(),
                confidence="medium",
                listing_status="coming_soon",
                notes="COMING SOON (BMR rental flyer)",
                document_url=document_url,
                source_url=document_url,
                page_number=page_number,
                raw_line=m.group(0),
            )
        )

    # Require at least 2 real properties (not just coming soon)
    named = [r for r in records if r.property_name and r.property_name != "Unknown Property"]
    if len(named) < 2 and not any(r.listing_status == "coming_soon" for r in records):
        return []
    if len(records) < 2:
        return []
    return records


def _extract_contact_directory_from_pdf(
    pdf_bytes: bytes,
    authority: str,
    document_url: str,
) -> list[HousingRecord]:
    """Multi-property BMR contact-card pages (Los Altos rental flyer style)."""
    records: list[HousingRecord] = []
    try:
        for page_idx, text in _iter_pdf_page_text(pdf_bytes):
            page_records = extract_bmr_contact_directory(
                text,
                authority=authority,
                document_url=document_url,
                page_number=page_idx,
            )
            if page_records:
                records.extend(page_records)
    except Exception as exc:
        logger.warning("[pdf] Contact-directory extraction failed for %s: %s", document_url, exc)
    return records


def extract_records_from_pdf(
    pdf_url: str,
    authority: str = "City of Gilroy",
    include_low_confidence: bool = False,
) -> list[HousingRecord]:
    """
    High-level entry point.
    Tries table extraction first, then flyer pages, then BMR contact-directory
    prose cards, then line-by-line parsing, then marker-pdf (optional).

    Fetch/policy failures raise SourceFetchError so dispatch marks SCRAPE_FAILED
    (#1076). A successful PDF with zero parseable rows still returns [] (real empty).
    """
    from housing_list_search.access import SourceFetchError

    real_url, _wrapper = normalize_docaccess_url(pdf_url)
    logger.info("[pdf] Extracting: %s", real_url)

    # Single fetch for table + flyer + line + marker cascade (#1080 / #1076)
    try:
        pdf_bytes = _fetch_pdf(real_url)
    except Exception as e:
        logger.warning("[pdf] Failed to fetch %s: %s", real_url, e)
        raise SourceFetchError(f"pdf: fetch failed for {real_url}: {e}") from e

    if not pdf_bytes or not _looks_like_pdf(pdf_bytes):
        raise SourceFetchError(
            f"pdf: response is not a PDF for {real_url}"
        )

    table_records = extract_records_from_pdf_bytes(pdf_bytes, authority=authority, document_url=real_url)
    if table_records:
        if not include_low_confidence:
            table_records = [r for r in table_records if r.confidence != "low"]
        if table_records:
            logger.info(
                "[pdf] Table extraction produced %d records from %s",
                len(table_records),
                real_url,
            )
            return table_records

    flyer_records = _extract_flyer_pages_from_pdf(pdf_bytes, authority, real_url)
    if flyer_records:
        logger.info(
            "[pdf] Flyer extraction produced %d records from %s", len(flyer_records), real_url
        )
        return flyer_records

    directory_records = _extract_contact_directory_from_pdf(pdf_bytes, authority, real_url)
    if directory_records:
        if not include_low_confidence:
            directory_records = [r for r in directory_records if r.confidence != "low"]
        if directory_records:
            logger.info(
                "[pdf] BMR contact-directory extraction produced %d records from %s",
                len(directory_records),
                real_url,
            )
            return directory_records

    text_lines = extract_text_lines_from_pdf(pdf_bytes)
    records: list[HousingRecord] = []
    for page_number, line in text_lines:
        rec = parse_housing_line(line, document_url=real_url, page_number=page_number)
        if rec:
            rec.authority = authority
            if include_low_confidence or rec.confidence != "low":
                records.append(rec)

    if records:
        logger.info("[pdf] Line extraction produced %d records from %s", len(records), real_url)
        return records

    # OCR tier: opt-in only (#1088). Never auto-load torch because package exists.
    from housing_list_search.extraction.marker_pdf import (
        extract_records_via_marker,
        marker_available,
        marker_ocr_explicitly_enabled,
    )

    if not marker_ocr_explicitly_enabled():
        logger.debug(
            "[pdf] Skipping marker OCR for %s — set HLS_ENABLE_MARKER_PDF=1 to opt in "
            "(ADR-0005; multi-GB models; not for daily --run)",
            real_url,
        )
        return []

    if not marker_available():
        logger.debug("[pdf] Marker OCR enabled but package unavailable for %s", real_url)
        return []

    marker_records = extract_records_via_marker(pdf_bytes, authority, real_url)
    if marker_records:
        if not include_low_confidence:
            marker_records = [r for r in marker_records if r.confidence != "low"]
        return marker_records

    return []


# ------------------------------------------------------------------
# Table Extraction (pdfplumber)
# ------------------------------------------------------------------


def extract_records_from_pdf_bytes(
    pdf_bytes: bytes,
    *,
    authority: str = "",
    document_url: str = "",
) -> list[HousingRecord]:
    """Table-aware extraction from already-fetched PDF bytes (pdfplumber)."""
    if pdfplumber is None or not pdf_bytes:
        return []

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
        with pdfplumber.open(io.BytesIO(pdf_bytes)) as pdf:
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
                        logger.debug(
                            "[pdf] No field_map built from header: %s", cleaned_rows[header_index]
                        )
                        continue
                    else:
                        logger.debug("[pdf] Field map for this table: %s", field_map)

                    for row in cleaned_rows[header_index + 1 :]:
                        if looks_like_header_row(row):
                            continue

                        rec = record_from_table_row(
                            row=row,
                            field_map=field_map,
                            document_url=document_url,
                            page_number=page_number,
                        )
                        if rec:
                            rec.authority = authority
                            records.append(rec)

    except Exception as e:
        logger.warning("[pdf] Table extraction failed for %s: %s", document_url, e)

    if records:
        logger.info(
            "[pdf] Table extraction produced %d records from %s", len(records), document_url
        )

    return records


def extract_records_from_pdf_tables(
    pdf_url: str,
    authority: str = "",
) -> list[HousingRecord]:
    """
    Table-aware extraction using pdfplumber.
    Preferred path for well-structured lists (like the Gilroy ones).

    Fetch failures raise SourceFetchError (#1076). Prefer
    extract_records_from_pdf() which cascades table→flyer→line on one fetch.
    """
    from housing_list_search.access import SourceFetchError

    if pdfplumber is None:
        return []

    pdf_url, _wrapper = normalize_docaccess_url(pdf_url)

    try:
        pdf_bytes = _fetch_pdf(pdf_url)
    except Exception as e:
        logger.warning("[pdf] Could not fetch %s: %s", pdf_url, e)
        raise SourceFetchError(f"pdf: fetch failed for {pdf_url}: {e}") from e

    return extract_records_from_pdf_bytes(
        pdf_bytes, authority=authority, document_url=pdf_url
    )
