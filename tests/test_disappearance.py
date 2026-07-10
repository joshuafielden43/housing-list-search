"""Tests for disappearance: machine Diff labels + staff projection (ADR-0001)."""

from housing_list_search.disappearance import (
    classify_machine_change,
    classify_machine_change_without_run_id,
    expand_scrape_failed_authorities,
    project_disappearance,
)


class TestClassifyMachineChange:
    def test_new_when_first_run_id_matches(self):
        assert (
            classify_machine_change(
                run_id="run1",
                last_run_id="run1",
                first_run_id="run1",
                authority="City",
            )
            == "NEW"
        )

    def test_new_legacy_null_first_run_id_and_equal_seen(self):
        assert (
            classify_machine_change(
                run_id="run1",
                last_run_id="run1",
                first_run_id=None,
                first_seen="t",
                last_seen="t",
                authority="City",
            )
            == "NEW"
        )

    def test_updated_when_confirmed_and_existed(self):
        assert (
            classify_machine_change(
                run_id="run2",
                last_run_id="run2",
                first_run_id="run1",
                authority="City",
            )
            == "UPDATED"
        )

    def test_scrape_failed_when_unconfirmed_and_failed_authority(self):
        failed = expand_scrape_failed_authorities(
            ["MidPen Housing (Santa Clara County portfolio)"]
        )
        assert "MidPen Housing" in failed
        assert (
            classify_machine_change(
                run_id="run2",
                last_run_id="run1",
                first_run_id="run1",
                authority="MidPen Housing",
                scrape_failed=failed,
            )
            == "SCRAPE_FAILED"
        )

    def test_stale_when_unconfirmed_and_scrape_ok(self):
        assert (
            classify_machine_change(
                run_id="run2",
                last_run_id="run1",
                first_run_id="run1",
                authority="City",
                scrape_failed=frozenset(),
            )
            == "STALE"
        )

    def test_without_run_id_stale_after_7_days(self):
        from datetime import datetime

        now = datetime(2026, 7, 4, 12, 0, 0)
        assert (
            classify_machine_change_without_run_id(
                first_seen="2019-01-01T00:00:00",
                last_seen="2020-01-01T00:00:00",
                now=now,
            )
            == "STALE"
        )
        assert (
            classify_machine_change_without_run_id(
                first_seen="2026-06-01T00:00:00",
                last_seen="2026-07-01T00:00:00",
                now=now,
            )
            == "UPDATED"
        )


class TestProjectDisappearance:
    def test_added_from_diff_new_rows(self):
        result = project_disappearance(
            run_id="run2",
            previous_run_id="run1",
            diff_rows=[
                {
                    "change_type": "NEW",
                    "source_authority": "City",
                    "property_name": "Oak",
                    "url": "https://oak",
                },
            ],
            current_listings=[{"authority": "City", "property_name": "Oak", "url": "https://oak"}],
            prev_snapshot=[{"authority": "City", "property_name": "Old", "url": ""}],
        )
        assert result.added == [("City", "Oak", "https://oak")]
        assert not result.removed

    def test_removed_when_stale_last_run_matches_previous(self):
        result = project_disappearance(
            run_id="run2",
            previous_run_id="run1",
            diff_rows=[
                {
                    "change_type": "STALE",
                    "source_authority": "City",
                    "property_name": "Gone",
                    "url": "",
                    "last_run_id": "run1",
                },
            ],
            current_listings=[],
            prev_snapshot=[{"authority": "City", "property_name": "Gone", "url": ""}],
        )
        # surrogate now scoped+hashed for stability (#983); compute via seam
        from housing_list_search.listing import listing_to_row
        expected_url = listing_to_row({"authority": "City", "property_name": "Gone", "url": ""})["url"]
        assert result.removed == [("City", "Gone", expected_url)]
        assert not result.stale_lingering

    def test_lingering_stale_when_last_run_older(self):
        result = project_disappearance(
            run_id="run2",
            previous_run_id="run1",
            diff_rows=[
                {
                    "change_type": "STALE",
                    "source_authority": "City",
                    "property_name": "Ancient",
                    "url": "https://old",
                    "last_run_id": "run0",
                },
            ],
            current_listings=[{"authority": "City", "property_name": "Stays", "url": "https://s"}],
            prev_snapshot=[{"authority": "City", "property_name": "Stays", "url": "https://s"}],
        )
        assert not result.removed
        assert result.stale_lingering == [("City", "Ancient", "https://old")]

    def test_scrape_failed_from_diff_only(self):
        result = project_disappearance(
            run_id="run2",
            previous_run_id="run1",
            diff_rows=[
                {
                    "change_type": "SCRAPE_FAILED",
                    "source_authority": "City B",
                    "property_name": "Lost",
                    "url": "https://b/1",
                },
            ],
            current_listings=[
                {"authority": "City A", "property_name": "Here", "url": "https://a/1"},
            ],
            prev_snapshot=[
                {"authority": "City B", "property_name": "Lost", "url": "https://b/1"},
                {"authority": "City A", "property_name": "Here", "url": "https://a/1"},
            ],
            scrape_failed_authorities=["City B"],
        )
        assert result.scrape_failed == [("City B", "Lost", "https://b/1")]
        assert not result.removed

    def test_status_change_from_run_prev_only(self):
        result = project_disappearance(
            run_id="run2",
            previous_run_id="run1",
            diff_rows=[],
            current_listings=[
                {
                    "authority": "City",
                    "property_name": "Oak",
                    "url": "https://x",
                    "status": "Open",
                },
            ],
            prev_snapshot=[
                {
                    "authority": "City",
                    "property_name": "Oak",
                    "url": "https://x",
                    "status": "Waitlist",
                },
            ],
        )
        assert len(result.status_changed) == 1
        assert result.status_changed[0][1:] == ("Waitlist", "Open")
