"""Tests for unified housing_registry.db schema ownership."""

from housing_list_search.schema import init_schema
from housing_list_search.sqlite_config import connect_sqlite


class TestInitSchema:
    def test_creates_all_tables(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = connect_sqlite(db_path)
        init_schema(conn)

        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert {"targets", "housing_records", "run_history"}.issubset(tables)

    def test_idempotent_second_call(self, tmp_path):
        db_path = tmp_path / "test.db"
        conn = connect_sqlite(db_path)
        init_schema(conn)
        init_schema(conn)
        count = conn.execute("SELECT COUNT(*) FROM targets").fetchone()[0]
        assert count == 0

    def test_database_manager_uses_same_schema(self, tmp_path):
        from housing_list_search.db import DatabaseManager

        db = DatabaseManager(tmp_path / "mgr.db")
        db.init_db()
        conn = db.connect()
        tables = {
            row[0]
            for row in conn.execute("SELECT name FROM sqlite_master WHERE type='table'").fetchall()
        }
        assert "targets" in tables
        assert "housing_records" in tables

    def test_registry_ingest_after_db_init(self, tmp_path, monkeypatch):
        from housing_list_search.db import DatabaseManager
        from housing_list_search.registry import get_all_targets, load_targets_to_db

        monkeypatch.chdir(tmp_path)
        monkeypatch.setattr("housing_list_search.registry.DB_PATH", str(tmp_path / "shared.db"))

        (tmp_path / "TARGETS.md").write_text(
            "City/Authority | URL | Notes | Scraping Measures | Priority | Last Seen\n"
            "--- | --- | --- | --- | --- | ---\n"
            "City A | https://a.example.gov/ | Notes | housekeys | High | 2026-07-01\n",
            encoding="utf-8",
        )

        DatabaseManager(tmp_path / "shared.db").init_db()
        load_targets_to_db()
        assert len(get_all_targets()) == 1
