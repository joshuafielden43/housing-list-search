"""
Unit tests for the Database Management Layer.

These tests create and destroy their own temporary databases.
"""

import tempfile
import os
from pathlib import Path
import pytest
import yaml

from housing_list_search.db import DatabaseManager


@pytest.fixture
def temp_db():
    """Provide a temporary database file for isolated testing."""
    with tempfile.TemporaryDirectory() as tmpdir:
        db_path = Path(tmpdir) / "test_housing_registry.db"
        mgr = DatabaseManager(db_path)
        mgr.init_db()
        yield mgr
        mgr.close()


def test_connect_enables_wal_and_busy_timeout(temp_db):
    conn = temp_db.connect()
    journal_mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    assert journal_mode.lower() == "wal"
    busy_timeout = conn.execute("PRAGMA busy_timeout").fetchone()[0]
    assert busy_timeout == 5000


def test_init_creates_tables(temp_db):
    mgr = temp_db
    count = mgr.get_record_count("housing_records")
    assert count == 0  # fresh


def test_prune_expires_at(temp_db):
    mgr = temp_db
    conn = mgr.connect()
    c = conn.cursor()

    # Insert test data
    c.execute("""
        INSERT INTO housing_records (authority, property_name, last_seen, expires_at)
        VALUES ('Test City', 'Old Expired', '2020-01-01', '2020-01-01')
    """)
    c.execute("""
        INSERT INTO housing_records (authority, property_name, last_seen, expires_at)
        VALUES ('Test City', 'Still Good', '2025-05-01', '2030-01-01')
    """)
    conn.commit()

    result = mgr.prune(expires_at_past=True, dry_run=False)
    assert result["deleted"] == 1
    assert mgr.get_record_count() == 1


def test_prune_not_seen_since(temp_db):
    mgr = temp_db
    conn = mgr.connect()
    c = conn.cursor()

    c.execute("""
        INSERT INTO housing_records (authority, property_name, last_seen)
        VALUES ('Test City', 'Very Old', '2020-01-01')
    """)
    c.execute("""
        INSERT INTO housing_records (authority, property_name, last_seen)
        VALUES ('Test City', 'Recent', '2025-05-20')
    """)
    conn.commit()

    result = mgr.prune(not_seen_since_days=30, dry_run=False)
    assert result["deleted"] >= 1


def test_export_diff_csv_marks_scrape_failed_separate_from_stale(temp_db):
    mgr = temp_db
    mgr.upsert_listings([
        {"authority": "City A", "property_name": "A1", "url": "https://a/1"},
        {"authority": "City B", "property_name": "B1", "url": "https://b/1"},
    ], run_id="prior")

    mgr.upsert_listings([
        {"authority": "City A", "property_name": "A1", "url": "https://a/1"},
    ], run_id="current")

    out = Path(tempfile.gettempdir()) / "test_diff_scrape_failed.csv"
    try:
        mgr.export_diff_csv(
            str(out),
            run_id="current",
            scrape_failed_authorities=["City B"],
        )
        import csv
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        by_auth = {r["source_authority"]: r["change_type"] for r in rows}
        assert by_auth["City A"] == "UPDATED"
        assert by_auth["City B"] == "SCRAPE_FAILED"
    finally:
        if out.exists():
            out.unlink()


def test_export_csv_includes_record_kind(temp_db):
    mgr = temp_db
    mgr.upsert_listings([
        {"authority": "City A", "property_name": "Oak Manor", "url": "https://a/1", "source": "midpen:find_housing", "address": "1 Oak"},
        {"authority": "City B", "property_name": "City B BMR (via HouseKeys)", "url": "https://hk.example/", "source": "housekeys:city_b", "administrator": "HouseKeys"},
    ], run_id="run1")

    out = Path(tempfile.gettempdir()) / "test_export_record_kind.csv"
    try:
        mgr.export_csv(str(out))
        import csv
        rows = list(csv.DictReader(out.read_text(encoding="utf-8").splitlines()))
        kinds = {r["property_name"]: r["record_kind"] for r in rows}
        assert kinds["Oak Manor"] == "property"
        assert kinds["City B BMR (via HouseKeys)"] == "portal"
    finally:
        if out.exists():
            out.unlink()


def test_export_csv_escapes_formula_injection(temp_db):
    mgr = temp_db
    mgr.upsert_listings([{
        "authority": "Test City",
        "property_name": "=CMD|'/C calc'!A0",
        "url": "https://example.gov/1",
    }], run_id="testrun1")

    out = Path(tempfile.gettempdir()) / "test_export_formula.csv"
    try:
        mgr.export_csv(str(out))
        text = out.read_text(encoding="utf-8")
        assert "'=CMD" in text or "'''=CMD" not in text
        assert text.splitlines()[1].startswith("Test City,")
        assert "'=CMD" in text.splitlines()[1]
    finally:
        if out.exists():
            out.unlink()


def test_prune_all_stale_combines_rules(temp_db):
    mgr = temp_db
    conn = mgr.connect()
    c = conn.cursor()

    c.execute("""
        INSERT INTO housing_records (authority, property_name, last_seen, expires_at)
        VALUES ('Test City', 'Expired Old', '2020-01-01', '2020-01-01')
    """)
    conn.commit()

    result = mgr.prune(all_stale=True)
    assert result["deleted"] >= 1


def test_snapshot_creates_archive(temp_db, tmp_path):
    import json
    import os
    import tarfile

    mgr = temp_db
    orig = os.getcwd()
    os.chdir(tmp_path)
    try:
        Path("current_full.csv").write_text("test,data\n1,2\n", encoding="utf-8")

        conn = mgr.connect()
        conn.execute(
            "INSERT INTO housing_records (authority, property_name, last_seen) VALUES (?, ?, ?)",
            ("Snapshot City", "Snapshot Prop", "2026-06-01"),
        )
        conn.commit()

        path = mgr.snapshot("test-snapshot")
        assert path.exists()
        assert path.suffix == ".tgz"

        with tarfile.open(path, "r:gz") as tar:
            names = tar.getnames()
            assert "manifest.json" in names
            assert "current_full.csv" in names
            assert "housing_registry.db" in names

            manifest = json.loads(tar.extractfile("manifest.json").read().decode("utf-8"))
            assert manifest["includes_db"] is True
            assert manifest["includes_csv"] is True
            assert manifest["record_count"] == 1
    finally:
        os.chdir(orig)


def test_settings_default_when_no_file(temp_db):
    mgr = temp_db
    settings = mgr._get_settings()
    assert settings["database"]["prune"]["default_not_seen_days"] == 45


def test_prune_from_diff_deletes_stale_rows(temp_db):
    mgr = temp_db
    mgr.upsert_listings([
        {"authority": "Old Auth", "property_name": "Gone", "url": "https://old.example/1"},
        {"authority": "Live Auth", "property_name": "Stays", "url": "https://live.example/1"},
    ], run_id="prior")

    diff_path = Path(tempfile.gettempdir()) / "test_prune_diff.csv"
    diff_path.write_text(
        "change_type,source_authority,property_name,url\n"
        "STALE,Old Auth,Gone,https://old.example/1\n",
        encoding="utf-8",
    )
    try:
        result = mgr.prune_from_diff(str(diff_path))
        assert result["deleted"] == 1
        assert mgr.get_record_count() == 1
    finally:
        diff_path.unlink(missing_ok=True)


def test_run_history_is_populated(temp_db):
    mgr = temp_db
    mgr.prune(all_stale=True)  # should log
    count = mgr._count_table("run_history")
    assert count >= 1