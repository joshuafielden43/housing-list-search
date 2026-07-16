"""
operator_maintenance.py — Operator Maintenance module (#1072).

Destructive / diagnostic DB ops: prune, snapshot, drop, info.
Composes an InventoryStore (or DatabaseManager facade) for connection + logging.
"""

from __future__ import annotations

import json
import subprocess
import tarfile
import tempfile
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any, Protocol

import yaml

SNAPSHOTS_DIR = Path("snapshots")
DEFAULT_SETTINGS_PATH = Path.home() / ".housing-list-search" / "settings.yaml"


class _StoreProtocol(Protocol):
    db_path: Path

    def connect(self) -> Any: ...
    def close(self) -> None: ...
    def get_record_count(self, table: str = "housing_records") -> int: ...
    def _log_run(self, *args: Any, **kwargs: Any) -> None: ...
    def init_db(self) -> bool: ...


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


class OperatorMaintenance:
    """Prune / snapshot / drop — operator tools, not the Run path."""

    def __init__(self, store: _StoreProtocol):
        self._store = store

    @property
    def db_path(self) -> Path:
        return self._store.db_path

    def connect(self):
        return self._store.connect()

    def close(self):
        return self._store.close()

    def get_record_count(self, table: str = "housing_records") -> int:
        return self._store.get_record_count(table)

    def _log_run(self, *args, **kwargs):
        return self._store._log_run(*args, **kwargs)

    def init_db(self) -> bool:
        return self._store.init_db()

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

    def drop_db(self, confirm: str = "") -> bool:
            """Drop the entire database file. Requires explicit confirmation."""
            if confirm != "DROP":
                raise ValueError("Must pass --confirm DROP to actually drop the database.")

            if self.db_path.exists():
                self.close()
                self.db_path.unlink()
            return True

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
