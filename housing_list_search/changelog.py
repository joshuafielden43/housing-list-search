# changelog.py
import csv
from datetime import datetime

from housing_list_search.csv_safety import sanitize_csv_field
from housing_list_search.freshness import (
    ListingKey,
    compute_run_diff,
    listing_identity,
    load_diff_csv_rows,
    stale_from_db_rows,
)


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
    """Write the current run's listing set to run_prev.csv for next-run diffing."""
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


def generate_changelog(
    current: list,
    skipped_targets=None,
    *,
    diff_csv_path: str = "diff.csv",
):
    """
    Diff run_prev against this run's listing set; enrich with STALE rows from diff.csv.

    current: deduped listing dicts from this run.
    diff_csv_path: machine diff written by RunPipeline (optional alignment source).
    """
    skipped_targets = skipped_targets or []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prev_items = _load_run_prev(_snapshot_path())
    run_diff = compute_run_diff(prev_items, current)
    is_first_run = not prev_items

    diff_rows = load_diff_csv_rows(diff_csv_path)
    removed_keys = set(run_diff.removed)
    stale_keys = stale_from_db_rows(diff_rows, removed_keys=removed_keys)

    # --- Markdown ---
    md = f"# Housing List Changelog\nRun: {timestamp}\n\n"

    if is_first_run:
        md += f"First run — {len(current)} listings loaded as baseline.\n\n"
    else:
        md += f"Previous: {len(prev_items)} listings | Current: {len(current)} listings\n\n"
        if run_diff.added:
            md += f"## ✅ New ({len(run_diff.added)})\n"
            for key in sorted(run_diff.added):
                md += f"- {_format_key(key)}\n"
            md += "\n"
        if run_diff.removed:
            md += f"## ❌ Removed ({len(run_diff.removed)})\n"
            for key in sorted(run_diff.removed):
                md += f"- {_format_key(key)}\n"
            md += "\n"
        if run_diff.status_changed:
            md += f"## 🔄 Status changed ({len(run_diff.status_changed)})\n"
            for key, old, new in sorted(run_diff.status_changed):
                auth, name, _url = key
                md += f"- {auth} — {name}: {old} → {new}\n"
            md += "\n"
        if stale_keys:
            md += f"## ⏳ Stale in DB ({len(stale_keys)})\n"
            md += (
                "These records were not confirmed this run (see diff.csv STALE). "
                "They may have closed or been removed from source.\n\n"
            )
            for key in sorted(stale_keys):
                md += f"- {_format_key(key)}\n"
            md += "\n"
        if (
            not run_diff.added
            and not run_diff.removed
            and not run_diff.status_changed
            and not stale_keys
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

    with open("changelog_diffs.md", "w", encoding="utf-8") as f:
        f.write(md)

    # --- CSV ---
    csv_rows: list[tuple[str, str, str, str, str]] = []
    if is_first_run:
        csv_rows.append(("INITIAL_RUN", "All Targets", "", "Initial population", timestamp))
    else:
        for auth, name, url in run_diff.added:
            csv_rows.append(("ADDED", auth, name, url, timestamp))
        for auth, name, url in run_diff.removed:
            csv_rows.append(("REMOVED", auth, name, url, timestamp))
        for (auth, name, url), old, new in run_diff.status_changed:
            csv_rows.append(("STATUS_CHANGE", auth, name, url, f"{old} → {new}"))
        for auth, name, url in stale_keys:
            csv_rows.append(("STALE", auth, name, url, "not confirmed this run"))
        if not csv_rows:
            csv_rows.append(("NO_CHANGE", "", "", "", f"{len(current)} listings unchanged"))

    for auth, _ in skipped_targets:
        csv_rows.append(("SKIPPED", "no_public_list", auth, "", timestamp))

    with open("changelog_diffs.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["change_type", "authority", "property_name", "url", "details"])
        writer.writerows([tuple(sanitize_csv_field(cell) for cell in row) for row in csv_rows])

    _write_run_snapshot(current)

    print(
        f"✅ Generated changelog_diffs.md (+{len(run_diff.added)} added, "
        f"-{len(run_diff.removed)} removed, {len(run_diff.status_changed)} status changes, "
        f"{len(stale_keys)} stale)"
    )
