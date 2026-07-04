"""RunPipeline integration tests — real dedupe + DB, fake adapter."""

import csv
from pathlib import Path

from housing_list_search.db import DatabaseManager
from housing_list_search.pipeline import RunPipeline


def _fake_listings_for(target: dict, failures: list[str] | None = None) -> list[dict]:
    return [
        {
            "authority": target["authority"],
            "property_name": f"{target['authority']} Prop",
            "url": target.get("url", ""),
            "listing_status": "open",
        }
    ]


class TestRunPipeline:
    def test_full_run_upserts_and_exports(self, tmp_path):
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            targets = [
                {"authority": "City A", "url": "https://a", "scraping_measures": ""},
                {"authority": "City B", "url": "https://b", "scraping_measures": ""},
            ]

            result = RunPipeline().run(
                targets,
                db=db,
                run_target_fn=_fake_listings_for,
                run_id="test-run-full",
            )

            assert len(result.listings) == 2
            assert result.inserted == 2
            assert result.n_full == 2
            assert Path("current_full.csv").exists()
            assert Path("diff.csv").exists()
        finally:
            os.chdir(orig)

    def test_partial_run_scopes_diff_and_preserves_run_prev(self, tmp_path):
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()
            db.upsert_listings(
                [
                    {
                        "authority": "City A",
                        "property_name": "A Prop",
                        "url": "https://a",
                        "listing_status": "open",
                    },
                    {
                        "authority": "City B",
                        "property_name": "B Prop",
                        "url": "https://b",
                        "listing_status": "open",
                    },
                ],
                run_id="prior-full",
            )

            run_prev = (
                "source_authority,property_name,status,listing_status\n"
                "City A,A Prop,Open,open\n"
                "City B,B Prop,Open,open\n"
            )
            Path("run_prev.csv").write_text(run_prev, encoding="utf-8")

            targets = [{"authority": "City A", "url": "https://a", "scraping_measures": ""}]

            result = RunPipeline().run(
                targets,
                db=db,
                partial_run=True,
                target_filter="City A",
                run_target_fn=lambda t, failures=None: [
                    {
                        "authority": "City A",
                        "property_name": "A Prop",
                        "url": "https://a",
                        "listing_status": "open",
                    }
                ],
                run_id="test-run-partial",
            )

            with open("diff.csv", newline="", encoding="utf-8") as f:
                diff_rows = list(csv.DictReader(f))
            with open("changelog_diffs.csv", newline="", encoding="utf-8") as f:
                changelog_rows = list(csv.DictReader(f))

            assert result.partial_run is True
            assert {r["source_authority"] for r in diff_rows} == {"City A"}
            assert not any(r["source_authority"] == "City B" for r in diff_rows)
            assert Path("run_prev.csv").read_text(encoding="utf-8") == run_prev
            assert changelog_rows[0]["change_type"] == "PARTIAL_RUN"
        finally:
            os.chdir(orig)

    def test_dedupe_runs_before_upsert(self, tmp_path):
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            dup_a = {
                "authority": "SCCHA",
                "property_name": "Oak Creek",
                "address": "100 Oak St, San Jose, CA",
                "url": "",
                "confidence": "high",
                "listing_status": "open",
            }
            dup_b = {
                "authority": "SJ Portal",
                "property_name": "Oak Creek",
                "address": "100 Oak St, San Jose, CA",
                "url": "",
                "confidence": "medium",
                "listing_status": "open",
            }

            def _dup_target(_t, failures=None):
                return [dup_a, dup_b]

            result = RunPipeline().run(
                [{"authority": "X", "url": "", "scraping_measures": ""}],
                db=db,
                run_target_fn=_dup_target,
                run_id="dedupe-test",
            )

            assert len(result.listings) == 1
            assert result.listings[0]["authority"] == "SCCHA"
            assert result.inserted == 1
        finally:
            os.chdir(orig)

    def test_failed_target_recorded(self, tmp_path):
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            def _boom(_t, failures):
                failures.append("City A")
                raise RuntimeError("adapter down")

            result = RunPipeline().run(
                [{"authority": "City A", "url": "https://a", "scraping_measures": ""}],
                db=db,
                run_target_fn=_boom,
            )

            assert result.failed_targets == ["City A"]
        finally:
            os.chdir(orig)

    def test_suspicious_zero_detected_without_failing_run(self, tmp_path):
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            def _empty_inventory(t, failures=None):
                return []

            targets = [
                {
                    "authority": "MidPen Housing",
                    "url": "https://midpen.example/",
                    "scraping_measures": "midpen,native_requests",
                },
                {
                    "authority": "City of Morgan Hill",
                    "url": "https://mh.example/",
                    "scraping_measures": "housekeys",
                },
            ]

            result = RunPipeline().run(
                targets,
                db=db,
                run_target_fn=_empty_inventory,
                run_id="suspicious-zero-test",
            )

            assert result.suspicious_zero_authorities == ["MidPen Housing"]
            assert result.failed_targets == []
            assert Path("daily_summary.md").exists()
            summary = Path("daily_summary.md").read_text(encoding="utf-8")
            assert "## Needs Review" in summary
            assert "MidPen Housing" in summary
            assert "City of Morgan Hill" not in summary.split("Needs Review", 1)[-1]
        finally:
            os.chdir(orig)

    def test_partial_run_scopes_reverification_to_matched_targets(self, tmp_path):
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            def _empty(_t, failures=None):
                return []

            targets = [
                {
                    "authority": "City of Campbell",
                    "url": "https://campbell.example/",
                    "scraping_measures": "civicplus",
                    "validated_zero": "2026-06-05",
                    "validated_zero_review_due": "2026-06-01",
                },
                {
                    "authority": "City of Cupertino",
                    "url": "https://cupertino.example/",
                    "scraping_measures": "gis",
                    "validated_zero": "2026-06-05",
                    "validated_zero_review_due": "2026-06-01",
                },
            ]

            RunPipeline().run(
                [targets[0]],
                db=db,
                partial_run=True,
                target_filter="Campbell",
                run_target_fn=_empty,
                run_id="partial-reverify-test",
            )

            summary = Path("daily_summary_partial.md").read_text(encoding="utf-8")
            assert "Campbell" in summary
            assert "Cupertino" not in summary.split("Needs Review", 1)[-1]
        finally:
            os.chdir(orig)

    def test_validated_zero_suppresses_suspicious_zero(self, tmp_path):
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            def _empty_inventory(_t, failures=None):
                return []

            targets = [
                {
                    "authority": "City of Campbell",
                    "url": "https://campbell.example/",
                    "scraping_measures": "civicplus,delegated_administrator",
                    "validated_zero": "2026-06-05",
                    "validated_zero_review_due": "2026-07-05",
                }
            ]

            result = RunPipeline().run(
                targets,
                db=db,
                run_target_fn=_empty_inventory,
                run_id="validated-zero-test",
            )

            assert result.suspicious_zero_authorities == []
            summary = Path("daily_summary.md").read_text(encoding="utf-8")
            assert "Suspicious zero" not in summary
        finally:
            os.chdir(orig)
