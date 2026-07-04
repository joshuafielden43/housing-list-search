# registry.py
"""
TARGETS.md → SQLite targets table.

Ingestion and sanitization for the `targets` table. DDL lives in schema.py.
"""

import logging
import re
import sqlite3

from housing_list_search.schema import init_schema
from housing_list_search.sqlite_config import DEFAULT_DB_PATH, connect_sqlite
from housing_list_search.url_policy import is_safe_http_url
from housing_list_search.validated_zero import parse_validated_zero_date

logger = logging.getLogger(__name__)

# Module-level path for tests that monkeypatch; defaults to shared constant.
DB_PATH = str(DEFAULT_DB_PATH)


def init_db(db_path: str | None = None) -> None:
    """Ensure housing_registry.db schema exists (delegates to schema.py)."""
    path = db_path or DB_PATH
    conn = connect_sqlite(path)
    init_schema(conn)
    conn.close()
    print("✅ SQLite registry initialized")


# ------------------------------------------------------------------
# Target sanitization / "nanny" layer
# ------------------------------------------------------------------
# We do not blindly trust every row in TARGETS.md.
# A bad or maliciously crafted entry could pollute outputs, cause
# routing errors, or (in future LLM contexts) create prompt injection.
# This function is the single place where we clean and validate.

MAX_AUTHORITY_LEN = 150
MAX_URL_LEN = 2048
MAX_NOTES_LEN = 800
MAX_ADMIN_LEN = 200
MAX_VALIDATED_ZERO_LEN = 40

ALLOWED_URL_SCHEMES = ("http://", "https://")

CONTROL_CHARS_RE = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f]")


def _clean_text(value: str, max_len: int) -> str:
    """Strip, remove control characters, truncate."""
    if not value:
        return ""
    value = value.strip()
    value = CONTROL_CHARS_RE.sub("", value)
    # Collapse multiple whitespace
    value = re.sub(r"\s+", " ", value)
    if len(value) > max_len:
        value = value[:max_len].rstrip()
    return value


def sanitize_target(raw: dict) -> dict:
    """
    Defensive ingestion for a target row parsed from TARGETS.md.

    Returns a cleaned dict. Logs warnings (does not raise) for anything
    that had to be repaired or looks suspicious.

    Rows whose URL is blank after sanitization are skipped on ingest.
    That is distinct from waf_blocked targets, which keep a valid URL in
    TARGETS.md but are skipped at scrape time in runner.py before any fetch.
    """
    out = {}

    # Authority (human name)
    authority = _clean_text(raw.get("authority", ""), MAX_AUTHORITY_LEN)
    if not authority:
        logger.warning(
            "Sanitizer: empty authority after cleaning — row will be skipped in practice"
        )
    out["authority"] = authority

    # URL — the highest-risk field
    url = (raw.get("url") or "").strip()
    original_url = url
    url = CONTROL_CHARS_RE.sub("", url)

    if not any(url.startswith(s) for s in ALLOWED_URL_SCHEMES):
        if url:
            logger.warning(
                f"Sanitizer: URL for '{authority}' has disallowed scheme or is malformed: {original_url[:100]}"
            )
        url = ""  # Will cause the row to be effectively inert
    elif not is_safe_http_url(url, resolve_dns=False):
        logger.warning(
            "Sanitizer: URL for '%s' failed outbound policy (SSRF/private) — cleared: %s",
            authority,
            original_url[:100],
        )
        url = ""

    if len(url) > MAX_URL_LEN:
        logger.warning(
            f"Sanitizer: URL for '{authority}' was truncated (was {len(original_url)} chars)"
        )
        url = url[:MAX_URL_LEN]

    out["url"] = url

    # Notes / free text
    notes = _clean_text(raw.get("notes", ""), MAX_NOTES_LEN)
    out["notes"] = notes

    # Scraping measures — normalize to clean lowercase comma list
    measures = raw.get("measures") or raw.get("scraping_measures") or ""
    measures = CONTROL_CHARS_RE.sub("", measures.lower())
    parts = [p.strip() for p in re.split(r"[,; ]+", measures) if p.strip()]
    # Dedupe while preserving order
    seen = set()
    clean_measures = []
    for p in parts:
        if p not in seen:
            seen.add(p)
            clean_measures.append(p)
    out["scraping_measures"] = ",".join(clean_measures)

    # Priority, last_seen — light cleaning
    out["priority"] = _clean_text(raw.get("priority", ""), 30)
    out["last_seen"] = _clean_text(raw.get("last_seen", ""), 30)

    # Administrator fields (injected context — still sanitize)
    for key in ("administrator", "administrator_phone", "administrator_contact"):
        val = _clean_text(raw.get(key, ""), MAX_ADMIN_LEN)
        out[key] = val

    admin_url = _clean_text(raw.get("administrator_url", ""), MAX_URL_LEN)
    if admin_url:
        admin_url = CONTROL_CHARS_RE.sub("", admin_url.strip())
        if not any(admin_url.startswith(s) for s in ALLOWED_URL_SCHEMES):
            logger.warning(
                "Sanitizer: administrator_url for '%s' has disallowed scheme — cleared: %s",
                authority,
                admin_url[:100],
            )
            admin_url = ""
        elif len(admin_url) > MAX_URL_LEN:
            logger.warning(
                "Sanitizer: administrator_url for '%s' was truncated (was %d chars)",
                authority,
                len(admin_url),
            )
            admin_url = admin_url[:MAX_URL_LEN]
        elif not is_safe_http_url(admin_url, resolve_dns=False):
            logger.warning(
                "Sanitizer: administrator_url for '%s' failed outbound policy — cleared",
                authority,
            )
            admin_url = ""
    out["administrator_url"] = admin_url

    # Detect potential prompt-injection style content in notes (future-proofing)
    suspicious = any(
        phrase in notes.lower()
        for phrase in ["ignore previous", "disregard", "system prompt", "you are now", "forget all"]
    )
    if suspicious:
        logger.warning(
            f"Sanitizer: notes for '{authority}' contained patterns that look like prompt injection attempts. They have been kept but should be reviewed by a human."
        )

    for key in ("validated_zero", "validated_zero_review_due"):
        raw_val = _clean_text(raw.get(key, ""), MAX_VALIDATED_ZERO_LEN)
        if raw_val and parse_validated_zero_date(raw_val) is None:
            logger.warning(
                "Sanitizer: %s for '%s' has no parseable ISO date — cleared: %s",
                key,
                authority,
                raw_val[:40],
            )
            raw_val = ""
        out[key] = raw_val

    return out


def load_targets_to_db():
    init_db()
    conn = connect_sqlite(DB_PATH)
    c = conn.cursor()

    # Clear and reload from markdown
    c.execute("DELETE FROM targets")

    with open("TARGETS.md", encoding="utf-8") as f:
        lines = f.readlines()

    in_table = False
    sanitized_count = 0
    skipped_count = 0

    for line in lines:
        if line.strip().startswith("City/Authority"):
            in_table = True
            continue
        if in_table and "|" in line and not line.startswith("---"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6:
                raw = {
                    "authority": parts[0],
                    "url": parts[1],
                    "notes": parts[2],
                    "scraping_measures": parts[3],
                    "priority": parts[4],
                    "last_seen": parts[5],
                    "administrator": parts[6] if len(parts) > 6 else "",
                    "administrator_url": parts[7] if len(parts) > 7 else "",
                    "administrator_phone": parts[8] if len(parts) > 8 else "",
                    "administrator_contact": parts[9] if len(parts) > 9 else "",
                    "validated_zero": parts[10] if len(parts) > 10 else "",
                    "validated_zero_review_due": parts[11] if len(parts) > 11 else "",
                }

                cleaned = sanitize_target(raw)

                # If the URL is empty after sanitization we treat it as a bad row
                if not cleaned["url"]:
                    logger.warning(
                        f"Sanitizer rejected row for authority='{raw['authority'][:60]}' — no usable URL after cleaning"
                    )
                    skipped_count += 1
                    continue

                c.execute(
                    """INSERT INTO targets 
                    (authority, url, notes, scraping_measures, priority, last_seen,
                     administrator, administrator_url, administrator_phone, administrator_contact,
                     validated_zero, validated_zero_review_due)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                    (
                        cleaned["authority"],
                        cleaned["url"],
                        cleaned["notes"],
                        cleaned["scraping_measures"],
                        cleaned["priority"],
                        cleaned["last_seen"],
                        cleaned["administrator"],
                        cleaned["administrator_url"],
                        cleaned["administrator_phone"],
                        cleaned["administrator_contact"],
                        cleaned["validated_zero"],
                        cleaned["validated_zero_review_due"],
                    ),
                )
                sanitized_count += 1

    conn.commit()
    conn.close()
    print(f"✅ Loaded targets into SQLite registry ({DB_PATH})")
    if sanitized_count:
        print(f"   Sanitizer processed {sanitized_count} rows")
    if skipped_count:
        print(
            f"   ⚠️  Sanitizer skipped {skipped_count} malformed row(s) — check TARGETS.md and logs"
        )


def get_all_targets():
    """Return all targets as list of dicts (for inspection / reporting)."""
    conn = connect_sqlite(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT authority, url, notes, scraping_measures, priority, last_seen,
               administrator, administrator_url, administrator_phone, administrator_contact,
               validated_zero, validated_zero_review_due
        FROM targets
        ORDER BY priority DESC, authority
    """)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def get_active_targets():
    """Targets that should be actively processed (excludes those marked no_public_list)."""
    conn = connect_sqlite(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT authority, url, notes, scraping_measures, priority, last_seen,
               administrator, administrator_url, administrator_phone, administrator_contact,
               validated_zero, validated_zero_review_due
        FROM targets
        WHERE scraping_measures IS NULL 
           OR scraping_measures NOT LIKE '%no_public_list%'
        ORDER BY priority DESC, authority
    """)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows


def get_skipped_targets():
    """Targets intentionally marked no_public_list (for reporting only)."""
    conn = connect_sqlite(DB_PATH)
    conn.row_factory = sqlite3.Row
    c = conn.cursor()
    c.execute("""
        SELECT authority, url, notes, scraping_measures, priority, last_seen,
               administrator, administrator_url, administrator_phone, administrator_contact,
               validated_zero, validated_zero_review_due
        FROM targets
        WHERE scraping_measures LIKE '%no_public_list%'
        ORDER BY authority
    """)
    rows = [dict(row) for row in c.fetchall()]
    conn.close()
    return rows
