"""
db.py — thin facade over Inventory Store + Operator Maintenance (#1072).

Prefer InventoryStore on the Run path (Machine Persist / Staff Publish).
Operator CLI (db_manage.py) uses DatabaseManager / get_manager() which exposes both.
"""

from __future__ import annotations

from pathlib import Path

from housing_list_search.inventory_store import (
    DB_PATH,
    DEFAULT_STALE_WARN_THRESHOLD,
    InventoryStore,
)
from housing_list_search.operator_maintenance import OperatorMaintenance

__all__ = [
    "DB_PATH",
    "DEFAULT_STALE_WARN_THRESHOLD",
    "InventoryStore",
    "OperatorMaintenance",
    "DatabaseManager",
    "get_manager",
]


class DatabaseManager(InventoryStore):
    """Backward-compatible facade: Inventory Store methods + operator maintenance."""

    def _maint(self) -> OperatorMaintenance:
        return OperatorMaintenance(self)

    def drop_db(self, confirm: str = "") -> bool:
        return self._maint().drop_db(confirm=confirm)

    def prune(
        self,
        not_seen_since_days: int | None = None,
        authority: str | None = None,
        dry_run: bool = False,
        all_stale: bool = False,
        expires_at_past: bool = False,
    ):
        return self._maint().prune(
            not_seen_since_days=not_seen_since_days,
            authority=authority,
            dry_run=dry_run,
            all_stale=all_stale,
            expires_at_past=expires_at_past,
        )

    def prune_from_diff(self, diff_path: str = "diff.csv", *, dry_run: bool = False):
        return self._maint().prune_from_diff(diff_path, dry_run=dry_run)

    def snapshot(self, name: str):
        return self._maint().snapshot(name)

    def list_snapshots(self):
        return self._maint().list_snapshots()

    def info(self):
        return self._maint().info()

    def _get_settings(self):
        """White-box tests / prune settings — delegated to Operator Maintenance."""
        return self._maint()._get_settings()

    def _count_table(self, table: str = "housing_records") -> int:
        return self._maint()._count_table(table)


def get_manager(db_path: Path | None = None) -> DatabaseManager:
    return DatabaseManager(db_path)
