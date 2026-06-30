# changelog.py
import csv
import os
from datetime import datetime
from pathlib import Path

from housing_list_search.csv_safety import sanitize_csv_field
from housing_list_search.status_labels import resolve_status_label


def _load_csv_keyed(path: str) -> dict[str, dict]:
    """Load a run snapshot CSV into a dict keyed by (source_authority, property_name)."""
    rows = {}
    try:
        with open(path, newline="", encoding="utf-8") as f:
            for row in csv.DictReader(f):
                key = (row.get("source_authority", ""), row.get("property_name", ""))
                rows[key] = row
    except FileNotFoundError:
        pass
    return rows


def _snapshot_path() -> str:
    """Path where the previous run's seen-this-run listing set is stored.

    This is NOT current_full.csv (which is a full DB export and retains records
    across runs). It is a lightweight CSV of exactly what was seen in the most
    recent run, used only for changelog diffing.
    """
    return "run_prev.csv"


def _write_run_snapshot(current: list) -> None:
    """Write the current run's listing set to run_prev.csv for next-run diffing."""
    with open(_snapshot_path(), "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["source_authority", "property_name", "status", "listing_status"])
        for item in current:
            auth = item.get("authority") or item.get("source_authority") or ""
            name = item.get("property_name") or ""
            status = item.get("status") or ""
            ls = item.get("listing_status") or ""
            writer.writerow([
                sanitize_csv_field(auth),
                sanitize_csv_field(name),
                sanitize_csv_field(status),
                sanitize_csv_field(ls),
            ])


def generate_changelog(current: list, skipped_targets=None):
    """
    Diff the previous run's seen-listings against the current run and write
    changelog_diffs.md + changelog_diffs.csv.

    current: list of raw listing dicts (pre-normalization) from this run.

    Baseline is run_prev.csv — written from the previous run's *deduped listing
    set*, not from current_full.csv. This means a record missing from this run
    is correctly reported as removed, even if the DB still holds it.
    """
    skipped_targets = skipped_targets or []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    prev_rows = _load_csv_keyed(_snapshot_path())
    # Build current keyed rows from the raw dicts passed in
    curr_rows: dict[tuple, dict] = {}
    for item in current:
        key = (item.get("authority", "") or item.get("source_authority", ""),
               item.get("property_name", ""))
        curr_rows[key] = item

    added = [k for k in curr_rows if k not in prev_rows]
    removed = [k for k in prev_rows if k not in curr_rows]

    # Status changes: key present in both but resolved display status differs.
    changed = []
    for k in curr_rows:
        if k not in prev_rows:
            continue
        old_status = resolve_status_label(prev_rows[k])
        new_status = resolve_status_label(curr_rows[k])
        if old_status and new_status and old_status != new_status:
            changed.append((k, old_status, new_status))

    is_first_run = not prev_rows

    # --- Markdown ---
    md = f"# Housing List Changelog\nRun: {timestamp}\n\n"

    if is_first_run:
        md += f"First run — {len(curr_rows)} listings loaded as baseline.\n\n"
    else:
        md += f"Previous: {len(prev_rows)} listings | Current: {len(curr_rows)} listings\n\n"
        if added:
            md += f"## ✅ New ({len(added)})\n"
            for auth, name in sorted(added):
                md += f"- {auth} — {name}\n"
            md += "\n"
        if removed:
            md += f"## ❌ Removed ({len(removed)})\n"
            for auth, name in sorted(removed):
                md += f"- {auth} — {name}\n"
            md += "\n"
        if changed:
            md += f"## 🔄 Status changed ({len(changed)})\n"
            for (auth, name), old, new in sorted(changed):
                md += f"- {auth} — {name}: {old} → {new}\n"
            md += "\n"
        if not added and not removed and not changed:
            md += "_No changes detected since last run._\n\n"

    if skipped_targets:
        md += "## ⚠️ Intentionally Skipped Targets (no_public_list)\n\n"
        md += ("These targets were skipped because they are marked in TARGETS.md as having no public "
               "structured BMR list or extractable portal.\n\n")
        for auth, note in skipped_targets:
            md += f"- {auth}"
            if note:
                md += f" — {note}"
            md += "\n"
        md += "\n"

    with open("changelog_diffs.md", "w", encoding="utf-8") as f:
        f.write(md)

    # --- CSV ---
    csv_rows = []
    if is_first_run:
        csv_rows.append(("INITIAL_RUN", "All Targets", "First Scrape", "Initial population", timestamp))
    else:
        for auth, name in added:
            csv_rows.append(("ADDED", auth, name, "", timestamp))
        for auth, name in removed:
            csv_rows.append(("REMOVED", auth, name, "", timestamp))
        for (auth, name), old, new in changed:
            csv_rows.append(("STATUS_CHANGE", auth, name, f"{old} → {new}", timestamp))
        if not csv_rows:
            csv_rows.append(("NO_CHANGE", "", "", f"{len(curr_rows)} listings unchanged", timestamp))

    for auth, _ in skipped_targets:
        csv_rows.append(("SKIPPED", "no_public_list", auth, "marked in TARGETS.md", timestamp))

    with open("changelog_diffs.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.writer(f)
        writer.writerow(["change_type", "authority", "property_name", "details", "timestamp"])
        writer.writerows([
            tuple(sanitize_csv_field(cell) for cell in row)
            for row in csv_rows
        ])

    # Snapshot the current run's listing set as the baseline for next-run diffing.
    # We snapshot from `current` (what was seen this run), NOT from current_full.csv
    # (which is a full DB export and retains records across runs — using it as a
    # diff baseline would cause removed records to appear as "removed" forever).
    _write_run_snapshot(current)

    print(f"✅ Generated changelog_diffs.md (+{len(added)} added, -{len(removed)} removed, "
          f"{len(changed)} status changes)")
