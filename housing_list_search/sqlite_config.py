"""Shared SQLite connection settings for housing_registry.db."""

from __future__ import annotations

import sqlite3
from pathlib import Path

DEFAULT_DB_PATH = Path("housing_registry.db")
SQLITE_BUSY_TIMEOUT_MS = 5000


def connect_sqlite(db_path: str | Path, *, timeout: float = 30.0) -> sqlite3.Connection:
    """Open a SQLite connection with WAL journaling and busy-timeout configured."""
    conn = sqlite3.connect(db_path, timeout=timeout)
    configure_sqlite_connection(conn)
    return conn


def configure_sqlite_connection(conn: sqlite3.Connection) -> None:
    conn.execute(f"PRAGMA busy_timeout = {SQLITE_BUSY_TIMEOUT_MS}")
    conn.execute("PRAGMA journal_mode = WAL")
