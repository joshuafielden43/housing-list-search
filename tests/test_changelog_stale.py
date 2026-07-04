"""Changelog + diff.csv STALE alignment."""

import csv
import os


def _run_changelog(tmp_path, current, diff_rows=None, scrape_failed_authorities=None):
    from housing_list_search.changelog import generate_changelog

    orig = os.getcwd()
    os.chdir(tmp_path)
    try:
        if diff_rows is not None:
            with open("diff.csv", "w", newline="", encoding="utf-8") as f:
                writer = csv.DictWriter(
                    f,
                    fieldnames=[
                        "change_type",
                        "source_authority",
                        "property_name",
                        "url",
                    ],
                )
                writer.writeheader()
                writer.writerows(diff_rows)
        generate_changelog(
            current,
            scrape_failed_authorities=scrape_failed_authorities,
        )
    finally:
        os.chdir(orig)


class TestChangelogStaleAlignment:
    def test_stale_in_diff_appears_when_not_in_run_snapshot(self, tmp_path):
        """DB STALE rows appear in changelog when not already reported as REMOVED."""
        run1 = [
            {
                "authority": "City",
                "property_name": "Stays",
                "url": "https://s",
                "listing_status": "open",
            }
        ]
        _run_changelog(tmp_path, run1)

        run2 = run1  # same run set — no REMOVED events
        diff_rows = [
            {
                "change_type": "STALE",
                "source_authority": "City",
                "property_name": "Ancient DB Record",
                "url": "https://old",
            },
        ]
        _run_changelog(tmp_path, run2, diff_rows=diff_rows)

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
        _run_changelog(tmp_path, run1)

        run2 = []
        diff_rows = [
            {
                "change_type": "STALE",
                "source_authority": "City",
                "property_name": "Gone",
                "url": "",
            },
        ]
        _run_changelog(tmp_path, run2, diff_rows=diff_rows)

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
        _run_changelog(tmp_path, run1)

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
            },
        ]
        _run_changelog(
            tmp_path,
            run2,
            diff_rows=diff_rows,
            scrape_failed_authorities=["City B"],
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
