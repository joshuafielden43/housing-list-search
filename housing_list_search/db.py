"""
Database Management Layer for Housing List Search.

This module provides the core logic for the bespoke "DBA in a box"
used for validation, testing, and future trash compactor work.

See PROJECT_CONTRACT_v0.8.2.md Section 7 for the governing contract.
"""

import sqlite3
import json
import os
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

import yaml

# Centralized paths
DB_PATH = Path("housing_registry.db")
SNAPSHOTS_DIR = Path("snapshots")
DEFAULT_SETTINGS_PATH = Path.home() / ".housing-list-search" / "settings.yaml"


class DatabaseManager:
    """Core manager for the housing database operations."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """Get or create a connection to the database."""
        if self.conn is None:
            self.conn = sqlite3.connect(self.db_path)
            self.conn.row_factory = sqlite3.Row
        return self.conn

    def close(self):
        if self.conn:
            self.conn.close()
            self.conn = None

    def _get_settings(self) -> Dict[str, Any]:
        """Load settings from YAML, with sensible defaults."""
        default_prune_days = 45
        if not DEFAULT_SETTINGS_PATH.exists():
            return {"database": {"prune": {"default_not_seen_days": default_prune_days}}}

        try:
            with open(DEFAULT_SETTINGS_PATH, "r", encoding="utf-8") as f:
                settings = yaml.safe_load(f) or {}
            prune_days = (
                settings.get("database", {})
                .get("prune", {})
                .get("default_not_seen_days", default_prune_days)
            )
            return {"database": {"prune": {"default_not_seen_days": prune_days}}}
        except Exception:
            return {"database": {"prune": {"default_not_seen_days": default_prune_days}}}

    def init_db(self, force: bool = False) -> bool:
        """Initialize the database schema if needed."""
        conn = self.connect()
        c = conn.cursor()

        # targets table (existing, keep compatible)
        c.execute("""
            CREATE TABLE IF NOT EXISTS targets (
                id INTEGER PRIMARY KEY,
                authority TEXT UNIQUE,
                url TEXT,
                notes TEXT,
                scraping_measures TEXT,
                priority TEXT,
                last_updated TEXT,
                administrator TEXT,
                administrator_url TEXT,
                administrator_phone TEXT,
                administrator_contact TEXT,
                active INTEGER DEFAULT 1
            )
        """)

        # housing_records table - the main data store for listings + freshness
        c.execute("""
            CREATE TABLE IF NOT EXISTS housing_records (
                id INTEGER PRIMARY KEY,
                authority TEXT,
                property_name TEXT,
                address TEXT,
                phone TEXT,
                email TEXT,
                url TEXT,
                status TEXT,
                deadline TEXT,
                notes TEXT,
                last_seen TEXT,
                first_seen TEXT,
                source TEXT,
                source_url TEXT,
                expires_at TEXT,
                raw_data TEXT,
                UNIQUE(authority, property_name, url)
            )
        """)

        # run_history for audit
        c.execute("""
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

        conn.commit()
        return True

    def drop_db(self, confirm: str = "") -> bool:
        """Drop the entire database file. Requires explicit confirmation."""
        if confirm != "DROP":
            raise ValueError("Must pass --confirm DROP to actually drop the database.")

        if self.db_path.exists():
            self.close()
            self.db_path.unlink()
        return True

    def get_record_count(self, table: str = "housing_records") -> int:
        conn = self.connect()
        c = conn.cursor()
        c.execute(f"SELECT COUNT(*) FROM {table}")
        return c.fetchone()[0]

    def _log_run(self, command: str, authority_filter: str = "", rows_before: int = 0, rows_after: int = 0, notes: str = ""):
        conn = self.connect()
        c = conn.cursor()
        c.execute("""
            INSERT INTO run_history (command, authority_filter, rows_before, rows_after, notes)
            VALUES (?, ?, ?, ?, ?)
        """, (command, authority_filter, rows_before, rows_after, notes))
        conn.commit()

    def prune(
        self,
        not_seen_since_days: Optional[int] = None,
        authority: Optional[str] = None,
        dry_run: bool = False,
        all_stale: bool = False,
        expires_at_past: bool = False,
    ) -> Dict[str, Any]:
        """Prune stale records according to contract rules."""
        settings = self._get_settings()
        default_days = settings["database"]["prune"]["default_not_seen_days"]
        days = not_seen_since_days if not_seen_since_days is not None else default_days

        conn = self.connect()
        c = conn.cursor()

        before = self.get_record_count()

        where_clauses = []
        params: List[Any] = []

        # Rule 1: expires_at in the past
        if all_stale or expires_at_past:
            where_clauses.append("expires_at IS NOT NULL AND expires_at < date('now')")

        # Rule 2: not seen within window
        if all_stale or not_seen_since_days is not None:
            cutoff = (datetime.now() - timedelta(days=days)).isoformat()
            where_clauses.append("last_seen < ?")
            params.append(cutoff)

        if not where_clauses:
            where_clauses.append("1=0")  # safety: do nothing unless criteria given

        where = " OR ".join(where_clauses) if where_clauses else "1=0"

        if authority:
            where = f"({where}) AND authority = ?"
            params.append(authority)

        if dry_run:
            c.execute(f"SELECT COUNT(*) FROM housing_records WHERE {where}", params)
            would_delete = c.fetchone()[0]
            return {"dry_run": True, "would_delete": would_delete, "before": before}

        c.execute(f"DELETE FROM housing_records WHERE {where}", params)
        conn.commit()

        after = self.get_record_count()
        self._log_run("prune", authority or "", before, after, f"not_seen_since_days={days}")

        return {"deleted": before - after, "before": before, "after": after}

    def snapshot(self, name: str) -> Path:
        """Create a snapshot .tgz of current state."""
        SNAPSHOTS_DIR.mkdir(exist_ok=True)

        timestamp = datetime.now().strftime("%Y%m%d_%H%M%S")
        safe_name = "".join(c for c in name if c.isalnum() or c in "-_").strip()
        archive_name = f"{safe_name}_{timestamp}.tgz"
        archive_path = SNAPSHOTS_DIR / archive_name

        # For now, we snapshot the CSV if it exists + manifest
        # In a full system we would also export housing_records
        csv_path = Path("current_full.csv")
        manifest = {
            "name": name,
            "created_at": datetime.now().isoformat(),
            "db_path": str(self.db_path),
            "record_count": self.get_record_count(),
            "git_commit": os.popen("git rev-parse --short HEAD 2>/dev/null || echo 'unknown'").read().strip(),
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            manifest_path = tmpdir / "manifest.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)

            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(manifest_path, arcname="manifest.json")
                if csv_path.exists():
                    tar.add(csv_path, arcname="current_full.csv")

        self._log_run("snapshot", "", 0, 0, f"name={name}")
        return archive_path

    def list_snapshots(self) -> List[Path]:
        if not SNAPSHOTS_DIR.exists():
            return []
        return sorted(SNAPSHOTS_DIR.glob("*.tgz"), reverse=True)

    def info(self) -> Dict[str, Any]:
        return {
            "db_path": str(self.db_path),
            "db_exists": self.db_path.exists(),
            "record_count": self.get_record_count() if self.db_path.exists() else 0,
            "run_history_count": self._count_table("run_history"),
        }

    def _count_table(self, table: str) -> int:
        try:
            conn = self.connect()
            c = conn.cursor()
            c.execute(f"SELECT COUNT(*) FROM {table}")
            return c.fetchone()[0]
        except Exception:
            return 0


def get_manager(db_path: Optional[Path] = None) -> DatabaseManager:
    return DatabaseManager(db_path)