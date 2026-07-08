"""Tests for disappearance projection (ADR-0001)."""

from housing_list_search.disappearance import project_disappearance


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
