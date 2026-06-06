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
                listing_status TEXT,
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

        # Lightweight migration: add listing_status if this DB was created before v0.8.6
        existing_cols = [row[1] for row in c.execute("PRAGMA table_info(housing_records)").fetchall()]
        if "listing_status" not in existing_cols:
            c.execute("ALTER TABLE housing_records ADD COLUMN listing_status TEXT")

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

    def upsert_listings(self, listings: list) -> dict:
        """
        Insert or update housing_records from a list of plain dicts.

        On conflict (same authority + property_name + url):
          - last_seen is always updated to now
          - status, listing_status, notes, source updated if non-empty
          - first_seen is preserved (never overwritten)
          - raw_data stores the full JSON of the most recent record

        Returns {"inserted": n, "updated": n}.
        """
        self.init_db()
        conn = self.connect()
        c = conn.cursor()
        now = datetime.now().isoformat()
        inserted = updated = 0

        for item in listings:
            if not isinstance(item, dict):
                item = item.to_dict() if hasattr(item, "to_dict") else vars(item)

            authority = (item.get("authority") or item.get("source_authority") or "").strip()
            property_name = (item.get("property_name") or "").strip()
            url = (item.get("url") or item.get("document_url") or "").strip()

            if not (authority and property_name):
                continue

            raw_json = json.dumps(item, default=str)
            last_seen = item.get("last_seen") or now
            first_seen_val = item.get("first_seen") or now
            status = (item.get("status") or "").strip()
            listing_status = (item.get("listing_status") or "").strip()
            notes = (item.get("notes") or "").strip()
            source = (item.get("source") or "").strip()
            source_url = (item.get("source_url") or item.get("document_url") or "").strip()
            expires_at = (item.get("expires_at") or "").strip()
            address = (item.get("address") or "").strip()
            phone = (item.get("phone") or "").strip()
            email = (item.get("email") or "").strip()
            deadline = (item.get("deadline") or "").strip()

            # Check if record exists
            c.execute(
                "SELECT id, first_seen FROM housing_records WHERE authority=? AND property_name=? AND url=?",
                (authority, property_name, url),
            )
            existing = c.fetchone()

            if existing:
                # Preserve original first_seen; update everything else
                c.execute("""
                    UPDATE housing_records SET
                        last_seen=?, status=?, listing_status=?, notes=?,
                        source=?, source_url=?, expires_at=?, address=?,
                        phone=?, email=?, deadline=?, raw_data=?
                    WHERE authority=? AND property_name=? AND url=?
                """, (
                    last_seen, status, listing_status, notes,
                    source, source_url, expires_at, address,
                    phone, email, deadline, raw_json,
                    authority, property_name, url,
                ))
                updated += 1
            else:
                c.execute("""
                    INSERT INTO housing_records
                        (authority, property_name, address, phone, email, url,
                         status, listing_status, deadline, notes,
                         last_seen, first_seen, source, source_url, expires_at, raw_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    authority, property_name, address, phone, email, url,
                    status, listing_status, deadline, notes,
                    last_seen, first_seen_val, source, source_url, expires_at, raw_json,
                ))
                inserted += 1

        conn.commit()
        self._log_run("upsert", "", inserted + updated, inserted + updated,
                      f"inserted={inserted} updated={updated}")
        return {"inserted": inserted, "updated": updated}

    def export_csv(self, path: str = "current_full.csv") -> int:
        """
        Export housing_records to a CSV file. Returns row count written.

        Column order matches the historical current_full.csv schema so existing
        importers and downstream tools require no changes.
        """
        import csv as _csv
        self.init_db()
        conn = self.connect()
        c = conn.cursor()
        c.execute("""
            SELECT
                authority        AS source_authority,
                property_name,
                address,
                phone,
                email,
                '' AS bedrooms,
                url,
                status,
                listing_status,
                deadline,
                '' AS income_limits,
                '' AS unit_types,
                '' AS eligibility_flags,
                notes,
                last_seen        AS scrape_date,
                '' AS confidence,
                '' AS administrator,
                '' AS administrator_url,
                '' AS administrator_phone,
                '' AS administrator_contact,
                last_seen,
                first_seen,
                source,
                source_url,
                expires_at
            FROM housing_records
            ORDER BY authority, property_name
        """)
        rows = c.fetchall()
        fieldnames = [d[0] for d in c.description]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(zip(fieldnames, row)))

        return len(rows)

    def export_diff_csv(self, path: str = "diff.csv") -> int:
        """
        Export a diff CSV: every housing_record row tagged with its change type.

        change_type values:
          NEW     — first_seen == last_seen (seen for the first time this run)
          UPDATED — last_seen > first_seen (re-seen; status may have changed)
          STALE   — last_seen older than 2 runs ago (rough heuristic; useful for
                    import pipelines that want to flag records not confirmed recently)

        The intent is that any competent DBA or AI can ingest this CSV and drive
        upserts into their own schema without knowing anything about this tool.
        """
        import csv as _csv
        self.init_db()
        conn = self.connect()
        c = conn.cursor()

        # Two-run staleness window: records not updated in the last 7 days
        c.execute("""
            SELECT
                CASE
                    WHEN first_seen = last_seen THEN 'NEW'
                    WHEN last_seen < datetime('now', '-7 days') THEN 'STALE'
                    ELSE 'UPDATED'
                END                 AS change_type,
                authority           AS source_authority,
                property_name,
                address,
                phone,
                email,
                url,
                status,
                listing_status,
                deadline,
                notes,
                last_seen,
                first_seen,
                source,
                source_url,
                expires_at
            FROM housing_records
            ORDER BY change_type, authority, property_name
        """)
        rows = c.fetchall()
        fieldnames = [d[0] for d in c.description]

        with open(path, "w", newline="", encoding="utf-8") as f:
            writer = _csv.DictWriter(f, fieldnames=fieldnames)
            writer.writeheader()
            for row in rows:
                writer.writerow(dict(zip(fieldnames, row)))

        return len(rows)


def get_manager(db_path: Optional[Path] = None) -> DatabaseManager:
    return DatabaseManager(db_path)