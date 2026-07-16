"""RunPipeline integration tests — real dedupe + DB, fake adapter."""

import csv
from pathlib import Path

from housing_list_search.db import DatabaseManager
from housing_list_search.dispatch import TargetScrapeResult
from housing_list_search.pipeline import RunPipeline


def _fake_listings_for(target: dict) -> TargetScrapeResult:
    auth = target["authority"]
    return TargetScrapeResult(
        authority=auth,
        records=[
            {
                "authority": auth,
                "property_name": f"{auth} Prop",
                "url": target.get("url", ""),
                "listing_status": "open",
            }
        ],
        had_error=False,
    )


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
            # Global machine baselines from a prior full run — must survive #241.
            global_full = "marker,global_full\nkeep,me\n"
            global_diff = "change_type,source_authority\nUPDATED,City B\n"
            Path("current_full.csv").write_text(global_full, encoding="utf-8")
            Path("diff.csv").write_text(global_diff, encoding="utf-8")

            targets = [{"authority": "City A", "url": "https://a", "scraping_measures": ""}]

            result = RunPipeline().run(
                targets,
                db=db,
                partial_run=True,
                target_filter="City A",
                run_target_fn=lambda t: TargetScrapeResult(
                    authority="City A",
                    records=[
                        {
                            "authority": "City A",
                            "property_name": "A Prop",
                            "url": "https://a",
                            "listing_status": "open",
                        }
                    ],
                    had_error=False,
                ),
                run_id="test-run-partial",
            )

            assert result.partial_run is True
            assert Path("run_prev.csv").read_text(encoding="utf-8") == run_prev
            assert Path("current_full.csv").read_text(encoding="utf-8") == global_full
            assert Path("diff.csv").read_text(encoding="utf-8") == global_diff
            assert Path("current_full_partial.csv").exists()
            assert Path("diff_partial.csv").exists()

            with open("diff_partial.csv", newline="", encoding="utf-8") as f:
                diff_rows = list(csv.DictReader(f))
            with open("changelog_diffs.csv", newline="", encoding="utf-8") as f:
                changelog_rows = list(csv.DictReader(f))

            assert {r["source_authority"] for r in diff_rows} == {"City A"}
            assert not any(r["source_authority"] == "City B" for r in diff_rows)
            assert changelog_rows[0]["change_type"] == "PARTIAL_RUN"
        finally:
            os.chdir(orig)

    def test_canonicalize_and_dedupe_before_upsert(self, tmp_path):
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

            def _dup_target(_t):
                return TargetScrapeResult(
                    authority="X",
                    records=[dup_a, dup_b],
                    had_error=False,
                )

            result = RunPipeline().run(
                [{"authority": "X", "url": "", "scraping_measures": ""}],
                db=db,
                run_target_fn=_dup_target,
                run_id="dedupe-test",
            )

            assert len(result.listings) == 1
            assert result.listings[0]["authority"] == "Santa Clara County Housing Authority"  # canonicalized (#983)
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

            def _boom(_t):
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

            def _empty_inventory(t):
                return TargetScrapeResult(
                    authority=t["authority"],
                    records=[],
                    had_error=False,
                )

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

            def _empty(_t):
                return TargetScrapeResult(
                    authority=_t["authority"],
                    records=[],
                    had_error=False,
                )

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

    def test_partial_run_mixed_failure_scrape_failed_not_stale_for_failed_authority(self, tmp_path):
        """Failed authority in partial run → SCRAPE_FAILED, not STALE for unselected."""
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

            def _mixed(t):
                if t["authority"] == "City A":
                    raise RuntimeError("adapter down")
                return TargetScrapeResult(
                    authority="City B",
                    records=[
                        {
                            "authority": "City B",
                            "property_name": "B Prop",
                            "url": "https://b",
                            "listing_status": "open",
                        }
                    ],
                    had_error=False,
                )

            targets = [
                {"authority": "City A", "url": "https://a", "scraping_measures": ""},
                {"authority": "City B", "url": "https://b", "scraping_measures": ""},
            ]

            result = RunPipeline().run(
                targets,
                db=db,
                partial_run=True,
                target_filter="City",
                run_target_fn=_mixed,
                run_id="partial-mixed-fail",
            )

            assert not Path("diff.csv").exists()  # #241: partial does not create global diff
            with open("diff_partial.csv", newline="", encoding="utf-8") as f:
                diff_rows = list(csv.DictReader(f))

            assert result.failed_targets == ["City A"]
            assert {r["source_authority"] for r in diff_rows} == {"City A", "City B"}
            by_auth = {
                (r["source_authority"], r["property_name"]): r["change_type"] for r in diff_rows
            }
            assert by_auth[("City A", "A Prop")] == "SCRAPE_FAILED"
            assert by_auth[("City B", "B Prop")] == "UPDATED"
            assert result.diff_counts.get("STALE", 0) == 0
        finally:
            os.chdir(orig)

    def test_exception_excludes_authority_from_suspicious_zero(self, tmp_path):
        """Scrape exception → failed_targets; must not also flag suspicious zero."""
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            def _boom(t):
                raise RuntimeError("down")

            targets = [
                {
                    "authority": "MidPen Housing",
                    "url": "https://midpen.example/",
                    "scraping_measures": "midpen,native_requests",
                }
            ]

            result = RunPipeline().run(
                targets,
                db=db,
                run_target_fn=_boom,
                run_id="exception-not-suspicious",
            )

            assert result.failed_targets == ["MidPen Housing"]
            assert result.suspicious_zero_authorities == []
        finally:
            os.chdir(orig)

    def test_failed_portfolio_label_canonicalized_for_scrape_failed(self, tmp_path):
        """#1049: TARGETS portfolio name → SCRAPE_FAILED on canonical MidPen Housing rows."""
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()
            db.upsert_listings(
                [
                    {
                        "authority": "MidPen Housing (Santa Clara County portfolio)",
                        "property_name": "MidPen Place",
                        "url": "https://midpen.example/p1",
                        "listing_status": "open",
                    },
                ],
                run_id="prior-good",
            )

            def _boom(t):
                raise RuntimeError("site down for the long weekend")

            targets = [
                {
                    "authority": "MidPen Housing (Santa Clara County portfolio)",
                    "url": "https://www.midpen-housing.org/find-housing/",
                    "scraping_measures": "midpen,native_requests",
                }
            ]

            result = RunPipeline().run(
                targets,
                db=db,
                run_target_fn=_boom,
                run_id="weekend-outage",
            )

            assert result.failed_targets == ["MidPen Housing"]
            with open("diff.csv", newline="", encoding="utf-8") as f:
                rows = list(csv.DictReader(f))
            assert len(rows) == 1
            assert rows[0]["source_authority"] == "MidPen Housing"
            assert rows[0]["change_type"] == "SCRAPE_FAILED"
            assert result.diff_counts.get("STALE", 0) == 0
            assert result.diff_counts.get("SCRAPE_FAILED", 0) == 1
        finally:
            os.chdir(orig)

    def test_full_run_with_failure_preserves_run_prev_baseline(self, tmp_path):
        """#1050: failed full run must not overwrite run_prev.csv with a thin set."""
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            # Seed a prior good baseline
            with open("run_prev.csv", "w", newline="", encoding="utf-8") as f:
                w = csv.writer(f)
                w.writerow(["authority", "property_name", "url", "listing_status"])
                w.writerow(["MidPen Housing", "Known Prop", "https://m/1", "open"])
            baseline = Path("run_prev.csv").read_text(encoding="utf-8")

            def _boom(t):
                raise RuntimeError("outage")

            result = RunPipeline().run(
                [
                    {
                        "authority": "MidPen Housing",
                        "url": "https://example/",
                        "scraping_measures": "midpen",
                    }
                ],
                db=db,
                run_target_fn=_boom,
                run_id="outage-run",
            )

            assert result.failed_targets == ["MidPen Housing"]
            assert Path("run_prev.csv").read_text(encoding="utf-8") == baseline
            assert Path("changelog_diffs.md").exists()
        finally:
            os.chdir(orig)

    def test_validated_zero_suppresses_suspicious_zero(self, tmp_path):
        import os

        orig = os.getcwd()
        os.chdir(tmp_path)
        try:
            db = DatabaseManager(Path("housing_registry.db"))
            db.init_db()

            def _empty_inventory(_t):
                return TargetScrapeResult(
                    authority=_t["authority"],
                    records=[],
                    had_error=False,
                )

            targets = [
                {
                    "authority": "City of Campbell",
                    "url": "https://campbell.example/",
                    "scraping_measures": "civicplus,delegated_administrator",
                    "validated_zero": "2026-06-05",
                    "validated_zero_review_due": "2026-08-01",
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
