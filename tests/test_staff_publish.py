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
    monkeypatch.setattr(
        "housing_list_search.staff_publish.write_proposed_prune",
        lambda **k: None,
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
    monkeypatch.setattr(
        "housing_list_search.staff_publish.write_proposed_prune",
        lambda **k: None,
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


def test_log_full_run_skipped_on_low_yield(tmp_path, monkeypatch):
    """#238: soft-thin portfolio must not advance disappearance baseline."""
    monkeypatch.chdir(tmp_path)
    db = MagicMock()
    db.get_previous_full_run_id.return_value = "run-prev-ok"
    captured: dict = {}

    def fake_changelog(*_a, **k):
        captured.update(k)

    monkeypatch.setattr(
        "housing_list_search.staff_publish.generate_changelog",
        fake_changelog,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.generate_daily_summary",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.surface_run_review",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.write_proposed_prune",
        lambda **k: None,
    )

    publish_staff_run(
        StaffPublishInput(
            listings=[],
            run_id="run-thin",
            targets_attempted=2,
            failed_targets=[],
            low_yield=[("MidPen Housing", 5)],
            inserted=5,
            updated=0,
        ),
        db=db,
    )
    db.log_full_run.assert_not_called()
    assert captured.get("update_run_prev") is False
    assert "MidPen Housing" in (captured.get("scrape_failed_authorities") or [])


def test_log_full_run_skipped_on_suspicious_zero(tmp_path, monkeypatch):
    """#238: empty inventory success must not promote prior rows to REMOVED."""
    monkeypatch.chdir(tmp_path)
    db = MagicMock()
    db.get_previous_full_run_id.return_value = "run-prev-ok"
    captured: dict = {}

    def fake_changelog(*_a, **k):
        captured.update(k)

    monkeypatch.setattr(
        "housing_list_search.staff_publish.generate_changelog",
        fake_changelog,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.generate_daily_summary",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.surface_run_review",
        lambda *a, **k: None,
    )
    monkeypatch.setattr(
        "housing_list_search.staff_publish.write_proposed_prune",
        lambda **k: None,
    )

    publish_staff_run(
        StaffPublishInput(
            listings=[],
            run_id="run-zero",
            targets_attempted=1,
            failed_targets=[],
            suspicious_zero_authorities=["Eden Housing"],
            inserted=0,
            updated=0,
        ),
        db=db,
    )
    db.log_full_run.assert_not_called()
    assert captured.get("update_run_prev") is False
    assert "Eden Housing" in (captured.get("scrape_failed_authorities") or [])


def test_write_proposed_prune_on_full_run(tmp_path, monkeypatch):
    """#240: full run writes proposed_prune.md with dry-run command."""
    from pathlib import Path

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
            run_id="run-prune",
            targets_attempted=1,
            failed_targets=[],
            stale_n=7,
            scrape_failed_n=2,
            inserted=0,
            updated=0,
        ),
        db=db,
    )
    text = Path("proposed_prune.md").read_text(encoding="utf-8")
    assert "STALE" in text
    assert "**7**" in text
    assert "SCRAPE_FAILED" in text
    assert "prune --from-diff" in text
    assert "--dry-run" in text
