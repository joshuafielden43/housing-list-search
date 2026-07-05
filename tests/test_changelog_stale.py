"""Changelog + diff.csv STALE alignment."""

import csv
import os


def _run_changelog(
    tmp_path,
    current,
    diff_rows=None,
    scrape_failed_authorities=None,
    *,
    run_id: str = "",
    previous_run_id: str | None = None,
):
    from housing_list_search.changelog import generate_changelog

    orig = os.getcwd()
    os.chdir(tmp_path)
    try:
        if diff_rows is not None:
            fieldnames = [
                "change_type",
                "source_authority",
                "property_name",
                "url",
                "last_run_id",
            ]
            with open("diff.csv", "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(f, fieldnames=fieldnames, extrasaction="ignore")
                writer.writeheader()
                writer.writerows(diff_rows)
        generate_changelog(
            current,
            scrape_failed_authorities=scrape_failed_authorities,
            run_id=run_id,
            previous_run_id=previous_run_id,
        )
    finally:
        os.chdir(orig)


class TestChangelogStaleAlignment:
    def test_stale_in_diff_appears_when_not_newly_removed(self, tmp_path):
        """Lingering DB STALE rows appear when last_run_id != previous_run_id."""
        run1 = [
            {
                "authority": "City",
                "property_name": "Stays",
                "url": "https://s",
                "listing_status": "open",
            }
        ]
        _run_changelog(tmp_path, run1, run_id="run1")

        run2 = run1
        diff_rows = [
            {
                "change_type": "STALE",
                "source_authority": "City",
                "property_name": "Ancient DB Record",
                "url": "https://old",
                "last_run_id": "run0",
            },
        ]
        _run_changelog(
            tmp_path,
            run2,
            diff_rows=diff_rows,
            run_id="run2",
            previous_run_id="run1",
        )

        md = open(tmp_path / "changelog_diffs.md", encoding="utf-8").read()
        assert "Stale in DB" in md
        assert "Ancient DB Record" in md

        with open(tmp_path / "changelog_diffs.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        stale = [r for r in rows if r["change_type"] == "STALE"]
        assert any(r["property_name"] == "Ancient DB Record" for r in stale)

    def test_removed_not_duplicated_as_stale(self, tmp_path):
        run1 = [
            {"authority": "City", "property_name": "Gone", "url": "", "listing_status": "open"},
        ]
        _run_changelog(tmp_path, run1, run_id="run1")

        run2 = []
        diff_rows = [
            {
                "change_type": "STALE",
                "source_authority": "City",
                "property_name": "Gone",
                "url": "",
                "last_run_id": "run1",
            },
        ]
        _run_changelog(
            tmp_path,
            run2,
            diff_rows=diff_rows,
            run_id="run2",
            previous_run_id="run1",
        )

        with open(tmp_path / "changelog_diffs.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert any(r["change_type"] == "REMOVED" for r in rows)
        assert not any(r["change_type"] == "STALE" for r in rows)

    def test_failed_authority_emits_scrape_failed_not_removed(self, tmp_path):
        run1 = [
            {
                "authority": "City B",
                "property_name": "Lost Listing",
                "url": "https://b/1",
                "listing_status": "open",
            },
            {
                "authority": "City A",
                "property_name": "Still Here",
                "url": "https://a/1",
                "listing_status": "open",
            },
        ]
        _run_changelog(tmp_path, run1, run_id="run1")

        run2 = [
            {
                "authority": "City A",
                "property_name": "Still Here",
                "url": "https://a/1",
                "listing_status": "open",
            },
        ]
        diff_rows = [
            {
                "change_type": "SCRAPE_FAILED",
                "source_authority": "City B",
                "property_name": "Lost Listing",
                "url": "https://b/1",
                "last_run_id": "run1",
            },
        ]
        _run_changelog(
            tmp_path,
            run2,
            diff_rows=diff_rows,
            scrape_failed_authorities=["City B"],
            run_id="run2",
            previous_run_id="run1",
        )

        md = open(tmp_path / "changelog_diffs.md", encoding="utf-8").read()
        assert "Scrape failed" in md
        assert "Lost Listing" in md
        assert "## ❌ Removed" not in md

        with open(tmp_path / "changelog_diffs.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        assert any(
            r["change_type"] == "SCRAPE_FAILED" and r["property_name"] == "Lost Listing"
            for r in rows
        )
        assert not any(r["change_type"] == "REMOVED" for r in rows)

    def test_added_from_diff_new_not_run_prev_snapshot(self, tmp_path):
        """ADDED projects from diff.csv NEW — not run_prev snapshot diff."""
        run1 = [
            {
                "authority": "City",
                "property_name": "Existing",
                "url": "https://e",
                "status": "Open",
            },
        ]
        _run_changelog(tmp_path, run1, run_id="run1")

        run2 = run1 + [
            {
                "authority": "City",
                "property_name": "Brand New",
                "url": "https://n",
                "status": "Open",
            },
        ]
        diff_rows = [
            {
                "change_type": "NEW",
                "source_authority": "City",
                "property_name": "Brand New",
                "url": "https://n",
            },
        ]
        _run_changelog(
            tmp_path,
            run2,
            diff_rows=diff_rows,
            run_id="run2",
            previous_run_id="run1",
        )

        with open(tmp_path / "changelog_diffs.csv", newline="", encoding="utf-8") as f:
            rows = list(csv.DictReader(f))
        added = [r for r in rows if r["change_type"] == "ADDED"]
        assert len(added) == 1
        assert added[0]["property_name"] == "Brand New"
