"""
Database Management Layer for Housing List Search.

This module provides the core logic for the bespoke "DBA in a box"
used for validation, testing, and future trash compactor work.

See PROJECT_CONTRACT_v0.8.6.md for the active contract.
"""

import json
import os
import sqlite3
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

import yaml

from housing_list_search.coverage import classify_record_kind
from housing_list_search.csv_safety import sanitize_csv_row
from housing_list_search.disappearance import (
    MACHINE_CHANGE_TYPES,
    classify_machine_change,
    classify_machine_change_without_run_id,
    expand_scrape_failed_authorities,
)
from housing_list_search.listing import canonicalize_listings
from housing_list_search.listing_identity import alias_matches
from housing_list_search.schema import init_schema
from housing_list_search.sqlite_config import DEFAULT_DB_PATH, connect_sqlite

# Centralized paths
DB_PATH = DEFAULT_DB_PATH
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

    def __init__(self, db_path: Path | None = None):
        self.db_path = db_path or DB_PATH
        self.conn: sqlite3.Connection | None = None

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

    def _get_settings(self) -> dict[str, Any]:
        """Load settings from YAML, with sensible defaults."""
        default_prune_days = 45
        if not DEFAULT_SETTINGS_PATH.exists():
            return {"database": {"prune": {"default_not_seen_days": default_prune_days}}}

        try:
            with open(DEFAULT_SETTINGS_PATH, encoding="utf-8") as f:
                settings = yaml.safe_load(f) or {}
            prune_days = (
                settings.get("database", {})
                .get("prune", {})
                .get("default_not_seen_days", default_prune_days)
            )
            return {"database": {"prune": {"default_not_seen_days": prune_days}}}
        except Exception:
            return {"database": {"prune": {"default_not_seen_days": default_prune_days}}}

    def init_db(self) -> bool:
        """Initialize all housing_registry.db tables (see schema.py)."""
        init_schema(self.connect())
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

    def _log_run(
        self,
        command: str,
        authority_filter: str = "",
        rows_before: int = 0,
        rows_after: int = 0,
        notes: str = "",
        *,
        run_id: str = "",
    ):
        conn = self.connect()
        c = conn.cursor()
        c.execute(
            """
            INSERT INTO run_history (command, authority_filter, rows_before, rows_after, notes, run_id)
            VALUES (?, ?, ?, ?, ?, ?)
        """,
            (command, authority_filter, rows_before, rows_after, notes, run_id or None),
        )
        conn.commit()

    def get_previous_full_run_id(self) -> str | None:
        """run_id of the most recent full --run logged before the current invocation."""
        self.init_db()
        conn = self.connect()
        row = conn.execute(
            """
            SELECT run_id FROM run_history
            WHERE command = '--run' AND run_id IS NOT NULL AND run_id != ''
            ORDER BY id DESC LIMIT 1
            """
        ).fetchone()
        if not row or not row[0]:
            return None
        return str(row[0])

    def log_full_run(self, run_id: str, *, rows_after: int = 0, notes: str = "") -> None:
        """Record a completed full --run for disappearance previous_run_id lookup."""
        self._log_run("--run", "", 0, rows_after, notes, run_id=run_id)

    def prune(
        self,
        not_seen_since_days: int | None = None,
        authority: str | None = None,
        dry_run: bool = False,
        all_stale: bool = False,
        expires_at_past: bool = False,
    ) -> dict[str, Any]:
        """Prune stale records according to contract rules."""
        settings = self._get_settings()
        default_days = settings["database"]["prune"]["default_not_seen_days"]
        days = not_seen_since_days if not_seen_since_days is not None else default_days

        conn = self.connect()
        c = conn.cursor()

        before = self.get_record_count()

        where_clauses = []
        params: list[Any] = []

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

    def prune_from_diff(
        self,
        diff_path: str = "diff.csv",
        *,
        dry_run: bool = False,
    ) -> dict[str, Any]:
        """
        Delete housing_records matching STALE rows in diff.csv.

        Uses the same identity key as upsert: (authority, property_name, url).
        Intended for post-migration cleanup when diff.csv shows paired NEW/STALE churn.
        """
        import csv as _csv

        stale_keys: list[tuple[str, str, str]] = []
        try:
            with open(diff_path, newline="", encoding="utf-8") as f:
                for row in _csv.DictReader(f):
                    if row.get("change_type") != "STALE":
                        continue
                    stale_keys.append(
                        (
                            (row.get("source_authority") or row.get("authority") or "").strip(),
                            (row.get("property_name") or "").strip(),
                            (row.get("url") or "").strip(),
                        )
                    )
        except FileNotFoundError:
            return {
                "deleted": 0,
                "before": self.get_record_count(),
                "after": self.get_record_count(),
                "stale_keys": 0,
            }

        before = self.get_record_count()
        if not stale_keys:
            return {"deleted": 0, "before": before, "after": before, "stale_keys": 0}

        conn = self.connect()
        c = conn.cursor()
        if dry_run:
            found = 0
            for auth, name, url in stale_keys:
                c.execute(
                    "SELECT 1 FROM housing_records WHERE authority=? AND property_name=? AND url=?",
                    (auth, name, url),
                )
                if c.fetchone():
                    found += 1
            return {
                "dry_run": True,
                "would_delete": found,
                "before": before,
                "stale_keys": len(stale_keys),
            }

        deleted = 0
        for auth, name, url in stale_keys:
            c.execute(
                "DELETE FROM housing_records WHERE authority=? AND property_name=? AND url=?",
                (auth, name, url),
            )
            deleted += c.rowcount
        conn.commit()
        after = self.get_record_count()
        self._log_run(
            "prune", "", before, after, f"from_diff stale_keys={len(stale_keys)} deleted={deleted}"
        )
        return {"deleted": deleted, "before": before, "after": after, "stale_keys": len(stale_keys)}

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

    def list_snapshots(self) -> list[Path]:
        if not SNAPSHOTS_DIR.exists():
            return []
        return sorted(SNAPSHOTS_DIR.glob("*.tgz"), reverse=True)

    def info(self) -> dict[str, Any]:
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

    def upsert_listings(
        self,
        listings: list,
        run_id: str = "",
        *,
        canonicalize: bool = True,
    ) -> dict:
        """
        Insert or update housing_records from Listing rows.

        run_id: opaque string identifying this run (e.g. ISO timestamp). Used by
            export_diff_csv() to tag NEW vs UPDATED reliably without relying on
            timestamp equality.

        canonicalize: when True (default), run ``canonicalize_listings`` so direct
            callers (tests, CLI) get the Listing seam. Machine Persist already
            canonicalizes before dedupe — pass ``canonicalize=False`` so the Store
            does not re-own shape policy on the Run path.

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

        # Listing shape: Machine Persist owns canonicalize on the Run path;
        # default True keeps a safe seam for direct upsert callers.
        canonical = canonicalize_listings(listings) if canonicalize else list(listings)

        # Collect normalized rows first
        to_upsert: list[dict] = []
        for row in canonical:
            authority = row["authority"]
            property_name = row["property_name"]
            url = row["url"]

            if not (authority and property_name):
                continue

            raw_json = json.dumps(row, default=str)
            to_upsert.append(
                {
                    "authority": authority,
                    "property_name": property_name,
                    "url": url,
                    "address": row["address"],
                    "phone": row["phone"],
                    "email": row["email"],
                    "deadline": row["deadline"],
                    "bedrooms": row["bedrooms"],
                    "income_limits": row["income_limits"],
                    "unit_types": row["unit_types"],
                    "eligibility_flags": row["eligibility_flags"],
                    "status": row["status"],
                    "listing_status": row["listing_status"],
                    "notes": row["notes"],
                    "confidence": row["confidence"],
                    "administrator": row["administrator"],
                    "administrator_url": row["administrator_url"],
                    "administrator_phone": row["administrator_phone"],
                    "administrator_contact": row["administrator_contact"],
                    "last_seen": row["last_seen"],
                    "first_seen": row["first_seen"],
                    "source": row["source"],
                    "source_url": row["source_url"],
                    "expires_at": row["expires_at"],
                    "raw_json": raw_json,
                    "run_id": run_id,
                }
            )

        if to_upsert:
            # One batch existence probe for inserted/updated counts (#786) — not per-row SELECT.
            existing: set[tuple[str, str, str]] = set()
            keys = [(r["authority"], r["property_name"], r["url"]) for r in to_upsert]
            # Chunk IN-lists to stay under SQLite variable limits
            chunk = 200
            for i in range(0, len(keys), chunk):
                part = keys[i : i + chunk]
                placeholders = ",".join("(?,?,?)" for _ in part)
                flat: list[str] = []
                for a, p, u in part:
                    flat.extend([a, p, u])
                c.execute(
                    f"SELECT authority, property_name, url FROM housing_records "
                    f"WHERE (authority, property_name, url) IN ({placeholders})",
                    flat,
                )
                for row in c.fetchall():
                    existing.add((row[0], row[1], row[2]))

            # Single-statement upsert using ON CONFLICT.
            upsert_sql = """
                INSERT INTO housing_records
                    (authority, property_name, address, phone, email, url,
                     status, listing_status, deadline, notes,
                     bedrooms, income_limits, unit_types, eligibility_flags,
                     confidence, administrator, administrator_url,
                     administrator_phone, administrator_contact,
                     last_seen, first_seen, last_run_id, first_run_id,
                     source, source_url, expires_at, raw_data)
                VALUES
                    (:authority, :property_name, :address, :phone, :email, :url,
                     :status, :listing_status, :deadline, :notes,
                     :bedrooms, :income_limits, :unit_types, :eligibility_flags,
                     :confidence, :administrator, :administrator_url,
                     :administrator_phone, :administrator_contact,
                     :last_seen, :first_seen, :run_id, :run_id,
                     :source, :source_url, :expires_at, :raw_json)
                ON CONFLICT (authority, property_name, url) DO UPDATE SET
                    last_seen = excluded.last_seen,
                    last_run_id = excluded.last_run_id,
                    status = excluded.status,
                    listing_status = excluded.listing_status,
                    notes = excluded.notes,
                    source = excluded.source,
                    source_url = excluded.source_url,
                    expires_at = excluded.expires_at,
                    address = excluded.address,
                    phone = excluded.phone,
                    email = excluded.email,
                    deadline = excluded.deadline,
                    bedrooms = excluded.bedrooms,
                    income_limits = excluded.income_limits,
                    unit_types = excluded.unit_types,
                    eligibility_flags = excluded.eligibility_flags,
                    confidence = excluded.confidence,
                    administrator = excluded.administrator,
                    administrator_url = excluded.administrator_url,
                    administrator_phone = excluded.administrator_phone,
                    administrator_contact = excluded.administrator_contact,
                    raw_data = excluded.raw_data,
                    first_seen = COALESCE(housing_records.first_seen, excluded.first_seen),
                    first_run_id = COALESCE(housing_records.first_run_id, excluded.first_run_id)
            """
            c.executemany(upsert_sql, to_upsert)

            for r in to_upsert:
                k = (r["authority"], r["property_name"], r["url"])
                if k in existing:
                    updated += 1
                else:
                    inserted += 1

        conn.commit()
        after = self.get_record_count()
        self._log_run(
            "upsert", "", after - inserted, after, f"inserted={inserted} updated={updated}"
        )
        return {"inserted": inserted, "updated": updated}

    def confirm_listing_identities(
        self,
        identities: list[tuple[str, str, str]] | set[tuple[str, str, str]],
        *,
        run_id: str,
    ) -> int:
        """Bump last_run_id for existing rows without changing content (#661 / #773).

        Used when cross-source dedupe drops a mirror authority's row: the property
        was still seen on that source this run, so it must not become STALE merely
        because a higher-scoring authority was chosen as the survivor. Does not
        INSERT missing keys (no fabricated inventory).
        """
        keys = [(a, p, u) for a, p, u in identities if a and p]
        if not keys or not run_id:
            return 0

        self.init_db()
        conn = self.connect()
        c = conn.cursor()
        now = datetime.now().isoformat()
        touched = 0
        chunk = 200
        for i in range(0, len(keys), chunk):
            part = keys[i : i + chunk]
            placeholders = ",".join("(?,?,?)" for _ in part)
            flat: list[str] = []
            for a, p, u in part:
                flat.extend([a, p, u])
            c.execute(
                f"""
                UPDATE housing_records
                SET last_run_id = ?,
                    last_seen = COALESCE(last_seen, ?)
                WHERE (authority, property_name, url) IN ({placeholders})
                """,
                [run_id, now, *flat],
            )
            touched += c.rowcount if c.rowcount and c.rowcount > 0 else 0
        conn.commit()
        if touched:
            self._log_run(
                "confirm_identities",
                "",
                0,
                self.get_record_count(),
                f"touched={touched} run_id={run_id}",
            )
        return touched

    def confirm_property_aliases(
        self,
        survivors: list[dict],
        *,
        run_id: str,
    ) -> int:
        """Confirm other authority/url rows for the same physical property (#1104).

        Match policy lives in listing_identity.alias_matches (pure). This method
        only loads candidates by property_name and executes last_run_id touches.
        """
        if not survivors or not run_id:
            return 0
        self.init_db()
        conn = self.connect()
        c = conn.cursor()
        now = datetime.now().isoformat()
        touched = 0
        for row in survivors:
            name = (row.get("property_name") or "").strip()
            if not name:
                continue
            c.execute(
                """
                SELECT authority, property_name, url, address
                FROM housing_records
                WHERE property_name = ?
                """,
                [name],
            )
            for auth, pname, url, addr in c.fetchall():
                candidate = {
                    "authority": auth or "",
                    "property_name": pname or "",
                    "url": url or "",
                    "address": addr or "",
                }
                if not alias_matches(row, candidate):
                    continue
                c.execute(
                    """
                    UPDATE housing_records
                    SET last_run_id = ?, last_seen = COALESCE(last_seen, ?)
                    WHERE authority = ? AND property_name = ? AND url = ?
                    """,
                    [run_id, now, candidate["authority"], candidate["property_name"], candidate["url"]],
                )
                touched += c.rowcount if c.rowcount and c.rowcount > 0 else 0
        conn.commit()
        return touched

    def export_csv(self, path: str = "current_full.csv", *, run_id: str | None = None) -> int:
        """
        Export housing_records to a CSV file. Returns row count written.

        Full known inventory (not this-run-only). When ``run_id`` is set, also
        appends ``last_run_id`` and ``confirmed_this_run`` (Y/N) so operators can
        filter live vs unconfirmed without changing the dump semantics (#659).
        """
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
                expires_at,
                last_run_id
            FROM housing_records
            ORDER BY authority, property_name
        """)
        rows = c.fetchall()
        fieldnames = [d[0] for d in c.description]
        fieldnames, rows = self._enrich_rows_with_record_kind(
            fieldnames, rows, run_id=run_id
        )

        self._write_csv_atomic(path, fieldnames, rows)
        return len(rows)

    @staticmethod
    def _enrich_rows_with_record_kind(
        fieldnames: list[str],
        rows,
        *,
        run_id: str | None = None,
    ) -> tuple[list[str], list[tuple]]:
        """Append derived record_kind (+ optional confirmed_this_run) columns."""
        out_fields = list(fieldnames)
        if "record_kind" not in out_fields:
            out_fields.append("record_kind")
        if run_id and "confirmed_this_run" not in out_fields:
            out_fields.append("confirmed_this_run")
        enriched: list[tuple] = []
        for row in rows:
            data = dict(zip(fieldnames, row))
            # classify_record_kind expects authority key
            if "authority" not in data and data.get("source_authority"):
                data["authority"] = data["source_authority"]
            data["record_kind"] = classify_record_kind(data)
            if run_id:
                data["confirmed_this_run"] = (
                    "Y" if (data.get("last_run_id") or "") == run_id else "N"
                )
            enriched.append(tuple(data.get(col, "") for col in out_fields))
        return out_fields, enriched

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

    # Columns written to diff.csv (change_type prepended after classify).
    _DIFF_EXPORT_FIELDS = (
        "source_authority",
        "property_name",
        "address",
        "phone",
        "email",
        "url",
        "status",
        "listing_status",
        "deadline",
        "bedrooms",
        "income_limits",
        "unit_types",
        "eligibility_flags",
        "confidence",
        "notes",
        "last_seen",
        "first_seen",
        "last_run_id",
        "source",
        "source_url",
        "expires_at",
    )

    def _fetch_records_for_diff(
        self,
        authorities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Load housing_records rows for Diff labeling (no change_type yet)."""
        self.init_db()
        conn = self.connect()
        c = conn.cursor()
        authorities = [a for a in (authorities or []) if a]
        where = ""
        params: list[str] = []
        if authorities:
            placeholders = ",".join("?" for _ in authorities)
            where = f" WHERE authority IN ({placeholders})"
            params = authorities
        # first_run_id is for classify_machine_change only — not exported.
        c.execute(
            f"""
            SELECT
                authority AS source_authority,
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
                last_run_id,
                first_run_id,
                source,
                source_url,
                expires_at
            FROM housing_records
            {where}
            ORDER BY authority, property_name
            """,
            params,
        )
        cols = [d[0] for d in c.description]
        return [dict(zip(cols, row)) for row in c.fetchall()]

    def _label_diff_rows(
        self,
        records: list[dict[str, Any]],
        *,
        run_id: str = "",
        scrape_failed_authorities: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Apply disappearance.classify_* to raw DB rows (single rule source)."""
        failed = expand_scrape_failed_authorities(scrape_failed_authorities)
        labeled: list[dict[str, Any]] = []
        for rec in records:
            if run_id:
                change = classify_machine_change(
                    run_id=run_id,
                    last_run_id=rec.get("last_run_id"),
                    first_run_id=rec.get("first_run_id"),
                    first_seen=rec.get("first_seen"),
                    last_seen=rec.get("last_seen"),
                    authority=str(rec.get("source_authority") or ""),
                    scrape_failed=failed,
                )
            else:
                change = classify_machine_change_without_run_id(
                    first_seen=rec.get("first_seen"),
                    last_seen=rec.get("last_seen"),
                )
            out = {"change_type": change}
            for col in self._DIFF_EXPORT_FIELDS:
                out[col] = rec.get(col, "")
            labeled.append(out)
        labeled.sort(
            key=lambda r: (
                r.get("change_type") or "",
                r.get("source_authority") or "",
                r.get("property_name") or "",
            )
        )
        return labeled

    def export_diff_csv(
        self,
        path: str = "diff.csv",
        run_id: str = "",
        authorities: list[str] | None = None,
        scrape_failed_authorities: list[str] | None = None,
    ) -> int:
        """
        Export a diff CSV: every housing_record row tagged with its change type.

        Labels come from ``disappearance.classify_machine_change`` (or the
        without-run_id fallback) — not from duplicated SQL CASE strings.

        change_type values:
          NEW            — confirmed this run; first confirmation is this run
          UPDATED        — confirmed this run but existed before
          SCRAPE_FAILED  — authority's scrape failed this run; not a closure
          STALE          — not confirmed in the current run

        run_id: the same run_id passed to upsert_listings(). When omitted,
            falls back to timestamp comparison (less reliable but still useful).

        authorities: optional source-authority scope for partial diagnostic runs.
            When provided, non-selected authorities are omitted instead of being
            reported as STALE.

        scrape_failed_authorities: authorities whose adapters raised this run.
            Their unconfirmed records are labelled SCRAPE_FAILED instead of STALE.
        """
        records = self._fetch_records_for_diff(authorities)
        labeled = self._label_diff_rows(
            records,
            run_id=run_id,
            scrape_failed_authorities=scrape_failed_authorities,
        )
        fieldnames = ["change_type", *self._DIFF_EXPORT_FIELDS]
        # enrich expects tuples aligned with fieldnames
        tuples = [tuple(row.get(col, "") for col in fieldnames) for row in labeled]
        fieldnames, tuples = self._enrich_rows_with_record_kind(fieldnames, tuples)

        self._write_csv_atomic(path, fieldnames, tuples)
        return len(tuples)

    def diff_counts(
        self,
        run_id: str,
        authorities: list[str] | None = None,
        scrape_failed_authorities: list[str] | None = None,
    ) -> dict[str, int]:
        """
        Count NEW / UPDATED / SCRAPE_FAILED / STALE using the same rules as export_diff_csv.

        Returns e.g. {"NEW": 3, "UPDATED": 40, "SCRAPE_FAILED": 2, "STALE": 12}.
        """
        records = self._fetch_records_for_diff(authorities)
        labeled = self._label_diff_rows(
            records,
            run_id=run_id,
            scrape_failed_authorities=scrape_failed_authorities,
        )
        counts: dict[str, int] = {k: 0 for k in MACHINE_CHANGE_TYPES}
        for row in labeled:
            ct = row.get("change_type") or "STALE"
            counts[ct] = counts.get(ct, 0) + 1
        for key in MACHINE_CHANGE_TYPES:
            counts.setdefault(key, 0)
        return counts


def get_manager(db_path: Path | None = None) -> DatabaseManager:
    return DatabaseManager(db_path)
