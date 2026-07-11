"""
machine_persist.py — deep Machine Persist module (#1070).

Owns post-collect machine-side policy: canonicalize → dedupe (with mirror
confirm set) → inventory upsert → machine CSV exports → STALE / SCRAPE_FAILED
operator thresholds.

Does not own: Target scrape (dispatch), Staff Publish artifacts, Disappearance
label math (disappearance.py). Callers: RunPipeline persist phase.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass, field

from housing_list_search.coverage import summarize_coverage
from housing_list_search.db import DEFAULT_STALE_WARN_THRESHOLD, DatabaseManager
from housing_list_search.dedupe import deduplicate_for_run
from housing_list_search.listing import canonicalize_listings

logger = logging.getLogger("housing_list_search")


@dataclass
class PersistResult:
    """DB + machine exports after canonicalize/dedupe (#782 / #1070 persist phase)."""

    listings: list[dict] = field(default_factory=list)
    run_id: str = ""
    inserted: int = 0
    updated: int = 0
    n_full: int = 0
    n_diff: int = 0
    diff_counts: dict[str, int] = field(default_factory=dict)
    scrape_failed_n: int = 0
    stale_n: int = 0
    cov_property: int = 0
    cov_portal: int = 0
    cov_program: int = 0
    cov_total: int = 0
    mirrors_confirmed: int = 0


def persist_run(
    listings_raw: list[dict],
    *,
    db: DatabaseManager,
    run_id: str,
    target_authorities: list[str] | None = None,
    failed_targets: list[str] | None = None,
    stale_warn_threshold: int = DEFAULT_STALE_WARN_THRESHOLD,
) -> PersistResult:
    """
    Persist one Run's machine inventory and exports.

    1. Canonicalize Listing rows
    2. Cross-source dedupe → survivors + mirrors_to_confirm (#1071)
    3. Upsert survivors; confirm mirror identities (no content overwrite)
    4. Coverage summary; current_full.csv + diff.csv; operator warn thresholds
    """
    failed = failed_targets or []
    all_listings = canonicalize_listings(listings_raw)
    deduped = deduplicate_for_run(all_listings, canonical=True)
    survivors = deduped.survivors

    # Survivors are already listing_to_row shape — do not re-canonicalize in Store.
    counts = db.upsert_listings(survivors, run_id=run_id, canonicalize=False)
    logger.info("DB upsert: %d inserted, %d updated", counts["inserted"], counts["updated"])

    mirrors_confirmed = 0
    if deduped.mirrors_to_confirm:
        mirrors_confirmed = db.confirm_listing_identities(
            deduped.mirrors_to_confirm, run_id=run_id
        )
        if mirrors_confirmed:
            logger.info(
                "Confirmed %d cross-source dedupe mirror(s) (no content overwrite; #661/#1071)",
                mirrors_confirmed,
            )

    # Pre-canon authority aliases still in DB (portfolio TARGETS strings) (#1104)
    alias_confirmed = db.confirm_property_aliases(survivors, run_id=run_id)
    if alias_confirmed:
        mirrors_confirmed += alias_confirmed
        logger.info(
            "Confirmed %d same-property alias row(s) (authority/url variants; #1104)",
            alias_confirmed,
        )

    cov = summarize_coverage(survivors)
    logger.info(
        "Coverage: %d property, %d portal, %d program (%d total)",
        cov.property_count,
        cov.portal_count,
        cov.program_count,
        cov.total,
    )

    n_full = db.export_csv("current_full.csv", run_id=run_id)
    n_diff = db.export_diff_csv(
        "diff.csv",
        run_id=run_id,
        authorities=target_authorities,
        scrape_failed_authorities=failed,
    )

    diff_counts = db.diff_counts(
        run_id,
        authorities=target_authorities,
        scrape_failed_authorities=failed,
    )
    scrape_failed_n = diff_counts.get("SCRAPE_FAILED", 0)
    if scrape_failed_n:
        logger.warning(
            "%d SCRAPE_FAILED record(s) in diff.csv — scrape errors, not confirmed closures",
            scrape_failed_n,
        )

    stale_n = diff_counts.get("STALE", 0)
    if stale_n >= stale_warn_threshold:
        logger.warning(
            "%d STALE record(s) in diff.csv (not confirmed this run; threshold=%d). "
            "Review diff.csv, then (safely) prune with: "
            "python scripts/db_manage.py prune --from-diff  [or --not-seen-since 45 after review]",
            stale_n,
            stale_warn_threshold,
        )
    elif stale_n > 0:
        logger.info(
            "%d STALE record(s) in diff.csv (below warn threshold of %d)",
            stale_n,
            stale_warn_threshold,
        )

    return PersistResult(
        listings=survivors,
        run_id=run_id,
        inserted=counts["inserted"],
        updated=counts["updated"],
        n_full=n_full,
        n_diff=n_diff,
        diff_counts=diff_counts,
        scrape_failed_n=scrape_failed_n,
        stale_n=stale_n,
        cov_property=cov.property_count,
        cov_portal=cov.portal_count,
        cov_program=cov.program_count,
        cov_total=cov.total,
        mirrors_confirmed=mirrors_confirmed,
    )
