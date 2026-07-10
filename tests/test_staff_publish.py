"""Staff Publish policy (#1063) — partial stubs without full pipeline."""

from pathlib import Path

from housing_list_search.staff_publish import write_partial_changelog_stubs


def test_write_partial_changelog_stubs(tmp_path, monkeypatch):
    monkeypatch.chdir(tmp_path)
    write_partial_changelog_stubs("City A")
    md = Path("changelog_diffs.md").read_text(encoding="utf-8")
    assert "Partial --target run" in md
    assert "City A" in md
    csv_text = Path("changelog_diffs.csv").read_text(encoding="utf-8")
    assert "PARTIAL_RUN" in csv_text
