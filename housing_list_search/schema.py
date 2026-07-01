"""
schema.py — sole owner of housing_registry.db DDL.

All tables (targets, housing_records, run_history) are created here.
registry.py ingests TARGETS.md rows; db.py persists listings — neither
module owns CREATE TABLE statements.
"""

from __future__ import annotations

import sqlite3


def _migrate_columns(
    cursor: sqlite3.Cursor,
    table: str,
    columns: list[tuple[str, str]],
) -> None:
    existing = {row[1] for row in cursor.execute(f"PRAGMA table_info({table})").fetchall()}
    for col, coltype in columns:
        if col not in existing:
            cursor.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")


def init_targets_schema(cursor: sqlite3.Cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS targets (
            id INTEGER PRIMARY KEY,
            authority TEXT,
            url TEXT,
            notes TEXT,
            scraping_measures TEXT,
            priority TEXT,
            last_seen TEXT,
            last_successful_scrape TEXT,
            confidence_score REAL DEFAULT 0.0,
            administrator TEXT,
            administrator_url TEXT,
            administrator_phone TEXT,
            administrator_contact TEXT
        )
    """)
    _migrate_columns(cursor, "targets", [
        ("administrator", "TEXT"),
        ("administrator_url", "TEXT"),
        ("administrator_phone", "TEXT"),
        ("administrator_contact", "TEXT"),
    ])


def init_housing_records_schema(cursor: sqlite3.Cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS housing_records (
            id INTEGER PRIMARY KEY,
            authority TEXT,
            property_name TEXT,
            address TEXT,
            phone TEXT,
            email TEXT,
            url TEXT,
            status TEXT,
            listing_status TEXT,
            deadline TEXT,
            notes TEXT,
            bedrooms TEXT,
            income_limits TEXT,
            unit_types TEXT,
            eligibility_flags TEXT,
            confidence TEXT,
            administrator TEXT,
            administrator_url TEXT,
            administrator_phone TEXT,
            administrator_contact TEXT,
            last_seen TEXT,
            first_seen TEXT,
            last_run_id TEXT,
            first_run_id TEXT,
            source TEXT,
            source_url TEXT,
            expires_at TEXT,
            raw_data TEXT,
            UNIQUE(authority, property_name, url)
        )
    """)
    _migrate_columns(cursor, "housing_records", [
        ("listing_status", "TEXT"),
        ("bedrooms", "TEXT"),
        ("income_limits", "TEXT"),
        ("unit_types", "TEXT"),
        ("eligibility_flags", "TEXT"),
        ("confidence", "TEXT"),
        ("administrator", "TEXT"),
        ("administrator_url", "TEXT"),
        ("administrator_phone", "TEXT"),
        ("administrator_contact", "TEXT"),
        ("last_run_id", "TEXT"),
        ("first_run_id", "TEXT"),
    ])


def init_run_history_schema(cursor: sqlite3.Cursor) -> None:
    cursor.execute("""
        CREATE TABLE IF NOT EXISTS run_history (
            id INTEGER PRIMARY KEY,
            timestamp TEXT DEFAULT CURRENT_TIMESTAMP,
            command TEXT,
            authority_filter TEXT,
            rows_before INTEGER,
            rows_after INTEGER,
            notes TEXT
        )
    """)


def init_schema(conn: sqlite3.Connection) -> None:
    """Create or migrate all housing_registry.db tables (idempotent)."""
    cursor = conn.cursor()
    init_targets_schema(cursor)
    init_housing_records_schema(cursor)
    init_run_history_schema(cursor)
    conn.commit()