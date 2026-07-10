"""#661 / #773: cross-source dedupe must not false-STALE the dropped authority."""

from __future__ import annotations

from pathlib import Path

from housing_list_search.db import DatabaseManager
from housing_list_search.dedupe import deduplicate_listings
from housing_list_search.dispatch import TargetScrapeResult
from housing_list_search.listing import canonicalize_listings, listing_identity
from housing_list_search.pipeline import RunPipeline


def test_confirm_listing_identities_bumps_last_run_id(tmp_path):
    db = DatabaseManager(tmp_path / "t.db")
    db.init_db()
    db.upsert_listings(
        [
            {
                "authority": "SJ Portal",
                "property_name": "Oak Creek",
                "address": "100 Oak St, San Jose, CA",
                "url": "",
                "confidence": "medium",
            }
        ],
        run_id="run1",
    )
    # After canonicalize the identity is stable
    row = canonicalize_listings(
        [
            {
                "authority": "SJ Portal",
                "property_name": "Oak Creek",
                "address": "100 Oak St, San Jose, CA",
                "url": "",
            }
        ]
    )[0]
    key = listing_identity(row)
    n = db.confirm_listing_identities([key], run_id="run2")
    assert n == 1
    counts = db.diff_counts("run2")
    assert counts["STALE"] == 0
    assert counts["UPDATED"] + counts["NEW"] >= 1


def test_pipeline_cross_source_dedupe_does_not_stale_mirror(tmp_path, monkeypatch):
    """Both authorities scrape the same address; lower-confidence authority stays confirmed."""
    import os

    orig = os.getcwd()
    os.chdir(tmp_path)
    try:
        db = DatabaseManager(Path("housing_registry.db"))
        db.init_db()

        # Prior run stored both authorities as separate rows (pre-dedupe history)
        prior = canonicalize_listings(
            [
                {
                    "authority": "SJ Portal",
                    "property_name": "Oak Creek",
                    "address": "100 Oak St, San Jose, CA",
                    "url": "",
                    "confidence": "medium",
                },
                {
                    "authority": "SCCHA",
                    "property_name": "Oak Creek",
                    "address": "100 Oak St, San Jose, CA",
                    "url": "",
                    "confidence": "high",
                },
            ]
        )
        db.upsert_listings(prior, run_id="run1")

        def scrape(target):
            auth = target["authority"]
            conf = "high" if "SCCHA" in auth or "Housing Authority" in auth else "medium"
            return TargetScrapeResult(
                authority=auth,
                records=[
                    {
                        "authority": auth,
                        "property_name": "Oak Creek",
                        "address": "100 Oak St, San Jose, CA",
                        "url": "",
                        "confidence": conf,
                    }
                ],
                had_error=False,
            )

        targets = [
            {
                "authority": "SCCHA",
                "url": "https://example.com/sccha",
                "scraping_measures": "john_stewart",
            },
            {
                "authority": "SJ Portal",
                "url": "https://example.com/sj",
                "scraping_measures": "bloom",
            },
        ]
        result = RunPipeline().run(
            targets,
            db=db,
            run_target_fn=scrape,
            run_id="run2",
        )
        # One survivor in this-run listing set after dedupe
        assert len(result.listings) == 1
        # Neither mirror should be STALE — both were seen this run
        assert result.diff_counts.get("STALE", 0) == 0
        assert result.stale_n == 0
    finally:
        os.chdir(orig)


def test_dedupe_still_drops_loser_from_run_set():
    """Content path still collapses to one row; only DB confirm is extra."""
    records = [
        {
            "property_name": "Oak Creek",
            "authority": "SCCHA",
            "address": "100 Oak St, San Jose, CA",
            "url": "",
            "confidence": "high",
        },
        {
            "property_name": "Oak Creek",
            "authority": "SJ Portal",
            "address": "100 Oak St, San Jose, CA",
            "url": "",
            "confidence": "medium",
        },
    ]
    result = deduplicate_listings(records)
    assert len(result) == 1
