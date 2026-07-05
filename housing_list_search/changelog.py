# changelog.py
import csv
from datetime import datetime

from housing_list_search.csv_safety import sanitize_csv_field
from housing_list_search.disappearance import DisappearanceResult, project_disappearance
from housing_list_search.freshness import ListingKey, listing_identity, load_diff_csv_rows


def _load_run_prev(path: str) -> list[dict]:
    """Load run_prev.csv rows as dicts (supports legacy rows without url)."""
    rows: list[dict] = []
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                rows.append(
                    {
                        "authority": row.get("source_authority", ""),
                        "property_name": row.get("property_name", ""),
                        "url": row.get("url", ""),
                        "status": row.get("status", ""),
                        "listing_status": row.get("listing_status", ""),
                    }
                )
    except FileNotFoundError:
        pass
    return rows


def _snapshot_path() -> str:
    """Lightweight CSV of listings seen in the most recent full run."""
    return "run_prev.csv"


def _write_run_snapshot(current: list) -> None:
    """Write the current run's listing set to run_prev.csv for next-run STATUS_CHANGE diff."""
    with open(_snapshot_path(), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(
            [
                "source_authority",
                "property_name",
                "url",
                "status",
                "listing_status",
            ]
        )
        for item in current:
            auth, name, url = listing_identity(item)
            status = item.get("status") or ""
            ls = item.get("listing_status") or ""
            writer.writerow(
                [
                    sanitize_csv_field(auth),
                    sanitize_csv_field(name),
                    sanitize_csv_field(url),
                    sanitize_csv_field(status),
                    sanitize_csv_field(ls),
                ]
            )


def _format_key(key: ListingKey) -> str:
    auth, name, url = key
    if url:
        return f"{auth} — {name} ({url})"
    return f"{auth} — {name}"


def render_changelog_markdown(
    result: DisappearanceResult,
    *,
    current_count: int,
    skipped_targets: list[tuple[str, str]],
    timestamp: str,
) -> str:
    """Render staff-facing changelog_diffs.md from a DisappearanceResult."""
    md = f"# Housing List Changelog\nRun: {timestamp}\n\n"

    if result.is_first_run:
        md += f"First run — {current_count} listings loaded as baseline.\n\n"
    else:
        md += (
            f"Previous: {result.prev_count} listings | Current: {result.current_count} listings\n\n"
        )
        if result.added:
            md += f"## ✅ New ({len(result.added)})\n"
            for key in sorted(result.added):
                md += f"- {_format_key(key)}\n"
            md += "\n"
        if result.removed:
            md += f"## ❌ Removed ({len(result.removed)})\n"
            for key in sorted(result.removed):
                md += f"- {_format_key(key)}\n"
            md += "\n"
        if result.scrape_failed:
            md += f"## ⚠️ Scrape failed ({len(result.scrape_failed)})\n"
            md += (
                "These records were not confirmed because the authority scrape failed — "
                "not evidence of closure. See diff.csv SCRAPE_FAILED.\n\n"
            )
            for key in sorted(result.scrape_failed):
                md += f"- {_format_key(key)}\n"
            md += "\n"
        if result.status_changed:
            md += f"## 🔄 Status changed ({len(result.status_changed)})\n"
            for key, old, new in sorted(result.status_changed):
                auth, name, _url = key
                md += f"- {auth} — {name}: {old} → {new}\n"
            md += "\n"
        if result.stale_lingering:
            md += f"## ⏳ Stale in DB ({len(result.stale_lingering)})\n"
            md += (
                "These records were not confirmed this run (see diff.csv STALE). "
                "They may have closed or been removed from source.\n\n"
            )
            for key in sorted(result.stale_lingering):
                md += f"- {_format_key(key)}\n"
            md += "\n"
        if (
            not result.added
            and not result.removed
            and not result.scrape_failed
            and not result.status_changed
            and not result.stale_lingering
        ):
            md += "_No changes detected since last run._\n\n"

    if skipped_targets:
        md += "## ⚠️ Intentionally Skipped Targets (no_public_list)\n\n"
        md += (
            "These targets were skipped because they are marked in TARGETS.md as having no public "
            "structured BMR list or extractable portal.\n\n"
        )
        for auth, note in skipped_targets:
            md += f"- {auth}"
            if note:
                md += f" — {note}"
            md += "\n"
        md += "\n"

    return md


def render_changelog_csv_rows(
    result: DisappearanceResult,
    *,
    current_count: int,
    skipped_targets: list[tuple[str, str]],
    timestamp: str,
) -> list[tuple[str, str, str, str, str]]:
    """Render changelog_diffs.csv rows from a DisappearanceResult."""
    csv_rows: list[tuple[str, str, str, str, str]] = []
    if result.is_first_run:
        csv_rows.append(("INITIAL_RUN", "All Targets", "", "Initial population", timestamp))
    else:
        for auth, name, url in result.added:
            csv_rows.append(("ADDED", auth, name, url, timestamp))
        for auth, name, url in result.removed:
            csv_rows.append(("REMOVED", auth, name, url, timestamp))
        for auth, name, url in result.scrape_failed:
            csv_rows.append(("SCRAPE_FAILED", auth, name, url, "authority scrape failed this run"))
        for (auth, name, url), old, new in result.status_changed:
            csv_rows.append(("STATUS_CHANGE", auth, name, url, f"{old} → {new}"))
        for auth, name, url in result.stale_lingering:
            csv_rows.append(("STALE", auth, name, url, "not confirmed this run"))
        if not csv_rows:
            csv_rows.append(("NO_CHANGE", "", "", "", f"{current_count} listings unchanged"))

    for auth, _ in skipped_targets:
        csv_rows.append(("SKIPPED", "no_public_list", auth, "", timestamp))

    return csv_rows


def generate_changelog(
    current: list,
    skipped_targets=None,
    *,
    run_id: str = "",
    previous_run_id: str | None = None,
    diff_csv_path: str = "diff.csv",
    scrape_failed_authorities: list[str] | None = None,
):
    """
    Project disappearance from diff.csv; render staff changelog artifacts.

    current: deduped listing dicts from this run.
    run_id / previous_run_id: drive REMOVED vs lingering STALE (from run_history).
    diff_csv_path: machine diff written by RunPipeline (source of truth per ADR-0001).
    """
    skipped_targets = skipped_targets or []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prev_items = _load_run_prev(_snapshot_path())
    diff_rows = load_diff_csv_rows(diff_csv_path)

    result = project_disappearance(
        run_id=run_id,
        previous_run_id=previous_run_id,
        diff_rows=diff_rows,
        current_listings=current,
        prev_snapshot=prev_items,
        scrape_failed_authorities=scrape_failed_authorities,
    )

    md = render_changelog_markdown(
        result,
        current_count=len(current),
        skipped_targets=skipped_targets,
        timestamp=timestamp,
    )
    with open("changelog_diffs.md", "w", encoding="utf-8") as f:
        f.write(md)

    csv_rows = render_changelog_csv_rows(
        result,
        current_count=len(current),
        skipped_targets=skipped_targets,
        timestamp=timestamp,
    )
    with open("changelog_diffs.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["change_type", "authority", "property_name", "url", "details"])
        writer.writerows([tuple(sanitize_csv_field(cell) for cell in row) for row in csv_rows])

    _write_run_snapshot(current)

    print(
        f"✅ Generated changelog_diffs.md (+{len(result.added)} added, "
        f"-{len(result.removed)} removed, {len(result.scrape_failed)} scrape_failed, "
        f"{len(result.status_changed)} status changes, {len(result.stale_lingering)} stale)"
    )
