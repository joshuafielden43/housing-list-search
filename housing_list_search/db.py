"""
Database Management Layer for Housing List Search.

This module provides the core logic for the bespoke "DBA in a box"
used for validation, testing, and future trash compactor work.

See PROJECT_CONTRACT_v0.8.6.md for the active contract.
"""

import sqlite3
import json
import os
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Optional, Dict, Any, List

import yaml

from housing_list_search.csv_safety import sanitize_csv_row
from housing_list_search.sqlite_config import connect_sqlite


# Centralized paths
DB_PATH = Path("housing_registry.db")
SNAPSHOTS_DIR = Path("snapshots")
DEFAULT_SETTINGS_PATH = Path.home() / ".housing-list-search" / "settings.yaml"

# Warn on --run when STALE rows in diff.csv meet or exceed this count.
DEFAULT_STALE_WARN_THRESHOLD = 5

def _git_short_commit() -> str:
    try:
        result = subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            timeout=5,
            check=False,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except Exception:
        pass
    return "unknown"


class DatabaseManager:
    """Core manager for the housing database operations."""

    def __init__(self, db_path: Optional[Path] = None):
        self.db_path = db_path or DB_PATH
        self.conn: Optional[sqlite3.Connection] = None

    def connect(self) -> sqlite3.Connection:
        """Get or create a connection to the database."""
        if self.conn is None:
            self.conn = connect_sqlite(self.db_path)
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

        # Targets live in registry.py (TARGETS.md → targets table). This module
        # owns housing_records and run_history only — see PROJECT_CONTRACT_v0.8.6.md.

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

        # Lightweight migration: add columns for DBs created before v0.8.6
        existing_cols = {row[1] for row in c.execute("PRAGMA table_info(housing_records)").fetchall()}
        for col, coltype in [
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
        ]:
            if col not in existing_cols:
                c.execute(f"ALTER TABLE housing_records ADD COLUMN {col} {coltype}")

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

        csv_path = Path("current_full.csv")
        includes_db = self.db_path.exists()
        includes_csv = csv_path.exists()
        manifest = {
            "name": name,
            "created_at": datetime.now().isoformat(),
            "db_path": str(self.db_path),
            "record_count": self.get_record_count(),
            "git_commit": _git_short_commit(),
            "includes_db": includes_db,
            "includes_csv": includes_csv,
        }

        with tempfile.TemporaryDirectory() as tmpdir:
            tmpdir = Path(tmpdir)
            manifest_path = tmpdir / "manifest.json"
            with open(manifest_path, "w") as f:
                json.dump(manifest, f, indent=2)

            with tarfile.open(archive_path, "w:gz") as tar:
                tar.add(manifest_path, arcname="manifest.json")
                if includes_csv:
                    tar.add(csv_path, arcname="current_full.csv")
                if includes_db:
                    tar.add(self.db_path, arcname="housing_registry.db")

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

    def upsert_listings(self, listings: list, run_id: str = "") -> dict:
        """
        Insert or update housing_records from a list of plain dicts.

        run_id: opaque string identifying this run (e.g. ISO timestamp). Used by
            export_diff_csv() to tag NEW vs UPDATED reliably without relying on
            timestamp equality.

        On conflict (same authority + property_name + url):
          - last_seen and last_run_id are always updated
          - all content fields updated to the latest values
          - first_seen is preserved (never overwritten)
          - raw_data stores the full JSON of the most recent record

        Returns {"inserted": n, "updated": n}.
        """
        self.init_db()
        conn = self.connect()
        c = conn.cursor()
        now = datetime.now().isoformat()
        if not run_id:
            run_id = now
        inserted = updated = 0

        from housing_list_search.listing import coerce_listing, listing_to_row

        for item in listings:
            raw = coerce_listing(item)
            row = listing_to_row(raw, now=now)

            authority = row["authority"]
            property_name = row["property_name"]
            url = row["url"]

            if not (authority and property_name):
                continue

            raw_json = json.dumps(raw, default=str)
            last_seen = row["last_seen"]
            first_seen_val = row["first_seen"]
            listing_status = row["listing_status"]
            status = row["status"]
            notes = row["notes"]
            source = row["source"]
            source_url = row["source_url"]
            expires_at = row["expires_at"]
            address = row["address"]
            phone = row["phone"]
            email = row["email"]
            deadline = row["deadline"]
            bedrooms = row["bedrooms"]
            income_limits = row["income_limits"]
            unit_types = row["unit_types"]
            eligibility_flags = row["eligibility_flags"]
            confidence = row["confidence"]
            administrator = row["administrator"]
            administrator_url = row["administrator_url"]
            administrator_phone = row["administrator_phone"]
            administrator_contact = row["administrator_contact"]

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
                        last_seen=?, last_run_id=?, status=?, listing_status=?, notes=?,
                        source=?, source_url=?, expires_at=?, address=?,
                        phone=?, email=?, deadline=?,
                        bedrooms=?, income_limits=?, unit_types=?, eligibility_flags=?,
                        confidence=?, administrator=?, administrator_url=?,
                        administrator_phone=?, administrator_contact=?, raw_data=?
                    WHERE authority=? AND property_name=? AND url=?
                """, (
                    last_seen, run_id, status, listing_status, notes,
                    source, source_url, expires_at, address,
                    phone, email, deadline,
                    bedrooms, income_limits, unit_types, eligibility_flags,
                    confidence, administrator, administrator_url,
                    administrator_phone, administrator_contact, raw_json,
                    authority, property_name, url,
                ))
                updated += 1
            else:
                c.execute("""
                    INSERT INTO housing_records
                        (authority, property_name, address, phone, email, url,
                         status, listing_status, deadline, notes,
                         bedrooms, income_limits, unit_types, eligibility_flags,
                         confidence, administrator, administrator_url,
                         administrator_phone, administrator_contact,
                         last_seen, first_seen, last_run_id, first_run_id, source, source_url, expires_at, raw_data)
                    VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """, (
                    authority, property_name, address, phone, email, url,
                    status, listing_status, deadline, notes,
                    bedrooms, income_limits, unit_types, eligibility_flags,
                    confidence, administrator, administrator_url,
                    administrator_phone, administrator_contact,
                    last_seen, first_seen_val, run_id, run_id, source, source_url, expires_at, raw_json,
                ))
                inserted += 1

        conn.commit()
        after = self.get_record_count()
        self._log_run("upsert", "", after - inserted, after,
                      f"inserted={inserted} updated={updated}")
        return {"inserted": inserted, "updated": updated}

    @staticmethod
    def _diff_case_sql(scrape_failed_authorities: Optional[list[str]] = None) -> tuple[str, list[str]]:
        """Build the SCRAPE_FAILED CASE branch and its bind parameters."""
        failed = [a for a in (scrape_failed_authorities or []) if a]
        if not failed:
            return "", []
        placeholders = ",".join("?" for _ in failed)
        branch = f"WHEN authority IN ({placeholders}) THEN 'SCRAPE_FAILED'\n                        "
        return branch, failed

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
                bedrooms,
                url,
                status,
                listing_status,
                deadline,
                income_limits,
                unit_types,
                eligibility_flags,
                notes,
                last_seen        AS scrape_date,
                confidence,
                administrator,
                administrator_url,
                administrator_phone,
                administrator_contact,
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

        self._write_csv_atomic(path, fieldnames, rows)
        return len(rows)

    @staticmethod
    def _write_csv_atomic(path: str, fieldnames: list[str], rows) -> None:
        """Write CSV via a same-directory temp file, then atomic replace."""
        import csv as _csv
        target = Path(path)
        target.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = None
        try:
            with tempfile.NamedTemporaryFile(
                mode="w",
                newline="",
                encoding="utf-8",
                dir=target.parent,
                prefix=f".{target.name}.",
                suffix=".tmp",
                delete=False,
            ) as f:
                tmp_path = f.name
                writer = _csv.DictWriter(f, fieldnames=fieldnames)
                writer.writeheader()
                for row in rows:
                    writer.writerow(sanitize_csv_row(dict(zip(fieldnames, row))))
            os.replace(tmp_path, target)
            tmp_path = None
        finally:
            if tmp_path and os.path.exists(tmp_path):
                os.unlink(tmp_path)

    def export_diff_csv(
        self,
        path: str = "diff.csv",
        run_id: str = "",
        authorities: Optional[list[str]] = None,
        scrape_failed_authorities: Optional[list[str]] = None,
    ) -> int:
        """
        Export a diff CSV: every housing_record row tagged with its change type.

        change_type values:
          NEW            — last_run_id matches the current run_id (first time seen this run)
          UPDATED        — confirmed this run but existed before (last_run_id == run_id, first_seen earlier)
          SCRAPE_FAILED  — authority's scrape failed this run; record not confirmed (not a closure)
          STALE          — not confirmed in the current run (last_run_id != run_id or no run_id given)

        run_id: the same run_id passed to upsert_listings(). When omitted,
            falls back to timestamp comparison (less reliable but still useful).

        authorities: optional source-authority scope for partial diagnostic runs.
            When provided, non-selected authorities are omitted instead of being
            reported as STALE.

        scrape_failed_authorities: authorities whose adapters raised this run.
            Their unconfirmed records are labelled SCRAPE_FAILED instead of STALE.

        The intent is that any competent DBA or AI can ingest this CSV and drive
        upserts into their own schema without knowing anything about this tool.
        """
        import csv as _csv
        self.init_db()
        conn = self.connect()
        c = conn.cursor()
        authorities = [a for a in (authorities or []) if a]
        where = ""
        authority_params: list[str] = []
        if authorities:
            placeholders = ",".join("?" for _ in authorities)
            where = f" WHERE authority IN ({placeholders})"
            authority_params = authorities

        scrape_failed_branch, scrape_failed_params = self._diff_case_sql(scrape_failed_authorities)

        if run_id:
            c.execute("""
                SELECT
                    CASE
                        WHEN last_run_id = ? AND (first_run_id = ? OR (first_run_id IS NULL AND first_seen = last_seen)) THEN 'NEW'
                        WHEN last_run_id = ? THEN 'UPDATED'
                        {scrape_failed_branch}ELSE 'STALE'
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
                    bedrooms,
                    income_limits,
                    unit_types,
                    eligibility_flags,
                    confidence,
                    notes,
                    last_seen,
                    first_seen,
                    source,
                    source_url,
                    expires_at
                FROM housing_records
                {where}
                ORDER BY change_type, authority, property_name
            """.format(where=where, scrape_failed_branch=scrape_failed_branch),
                [run_id, run_id, run_id, *scrape_failed_params, *authority_params])
        else:
            # Fallback when no run_id: STALE = not seen in 7 days
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
                    bedrooms,
                    income_limits,
                    unit_types,
                    eligibility_flags,
                    confidence,
                    notes,
                    last_seen,
                    first_seen,
                    source,
                    source_url,
                    expires_at
                FROM housing_records
                {where}
                ORDER BY change_type, authority, property_name
            """.format(where=where), authority_params)
        rows = c.fetchall()
        fieldnames = [d[0] for d in c.description]

        self._write_csv_atomic(path, fieldnames, rows)
        return len(rows)

    def diff_counts(
        self,
        run_id: str,
        authorities: Optional[list[str]] = None,
        scrape_failed_authorities: Optional[list[str]] = None,
    ) -> dict[str, int]:
        """
        Count NEW / UPDATED / SCRAPE_FAILED / STALE rows using export_diff_csv() rules.

        Returns e.g. {"NEW": 3, "UPDATED": 40, "SCRAPE_FAILED": 2, "STALE": 12}.
        authorities scopes partial diagnostic runs so unrelated records are not
        counted as STALE.
        """
        self.init_db()
        conn = self.connect()
        c = conn.cursor()
        authorities = [a for a in (authorities or []) if a]
        scrape_failed_branch, scrape_failed_params = self._diff_case_sql(scrape_failed_authorities)
        where = ""
        params: list[str] = [run_id, run_id, run_id, *scrape_failed_params]
        if authorities:
            placeholders = ",".join("?" for _ in authorities)
            where = f" WHERE authority IN ({placeholders})"
            params.extend(authorities)
        c.execute("""
            SELECT
                CASE
                    WHEN last_run_id = ? AND (first_run_id = ? OR (first_run_id IS NULL AND first_seen = last_seen)) THEN 'NEW'
                    WHEN last_run_id = ? THEN 'UPDATED'
                    {scrape_failed_branch}ELSE 'STALE'
                END AS change_type,
                COUNT(*) AS n
            FROM housing_records
            {where}
            GROUP BY change_type
        """.format(where=where, scrape_failed_branch=scrape_failed_branch), params)
        counts = {row[0]: row[1] for row in c.fetchall()}
        for key in ("NEW", "UPDATED", "SCRAPE_FAILED", "STALE"):
            counts.setdefault(key, 0)
        return counts


def get_manager(db_path: Optional[Path] = None) -> DatabaseManager:
    return DatabaseManager(db_path)
