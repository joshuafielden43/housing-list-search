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
    mgr = temp_db
    # Create a fake current_full.csv for snapshot test
    csv = Path("current_full.csv")
    csv.write_text("test,data\n1,2\n")

    try:
        path = mgr.snapshot("test-snapshot")
        assert path.exists()
        assert path.suffix == ".tgz"
    finally:
        if csv.exists():
            csv.unlink()


def test_settings_default_when_no_file(temp_db):
    mgr = temp_db
    settings = mgr._get_settings()
    assert settings["database"]["prune"]["default_not_seen_days"] == 45


def test_run_history_is_populated(temp_db):
    mgr = temp_db
    mgr.prune(all_stale=True)  # should log
    count = mgr._count_table("run_history")
    assert count >= 1