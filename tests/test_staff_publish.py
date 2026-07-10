"""Staff Publish policy (#1063 / #1085) — partial stubs without full pipeline."""

from pathlib import Path
from unittest.mock import MagicMock

from housing_list_search.staff_publish import (
    StaffPublishInput,
    publish_staff_run,
    write_partial_changelog_stubs,
)


def test_write_partial_changelog_stubs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_partial_changelog_stubs("City A")
    md = Path("changelog_diffs.md").read_text(encoding="utf-8")
    assert "Partial --target run" in md
    assert "City A" in md
    csv_text = Path("changelog_diffs.csv").read_text(encoding="utf-8")
    assert "PARTIAL_RUN" in csv_text


def test_log_full_run_skipped_when_targets_failed(tmp_path, monkeypatch):
    """#1085: failed full run must not become previous_full_run_id."""
    monkeypatch.chdir(tmp_path)
    db = MagicMock()
    db.get_previous_full_run_id.return_value = "run-prev-ok"
    monkeypatch.setattr(
        "housing_list_search.staff_publish.generate_changelog",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.generate_daily_summary",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.surface_run_review",
        lambda *a, **k: None,
    )

    publish_staff_run(
        StaffPublishInput(
            listings=[],
            run_id="run-failed",
            targets_attempted=2,
            failed_targets=["City A"],
            inserted=0,
            updated=0,
        ),
        db=db,
    )
    db.log_full_run.assert_not_called()


def test_log_full_run_on_clean_full_run(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    db = MagicMock()
    db.get_previous_full_run_id.return_value = None
    monkeypatch.setattr(
        "housing_list_search.staff_publish.generate_changelog",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.generate_daily_summary",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.surface_run_review",
        lambda *a, **k: None,
    )

    publish_staff_run(
        StaffPublishInput(
            listings=[],
            run_id="run-ok",
            targets_attempted=2,
            failed_targets=[],
            inserted=1,
            updated=2,
        ),
        db=db,
    )
    db.log_full_run.assert_called_once_with("run-ok", rows_after=3)
