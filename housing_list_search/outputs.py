# outputs.py
from datetime import datetime

PARTIAL_DAILY_SUMMARY_PATH = "daily_summary_partial.md"
STAFF_DAILY_SUMMARY_PATH = "daily_summary.md"


def _listing_is_open(listing: dict) -> bool:
    listing_status = (listing.get("listing_status") or "").lower()
    status_val = (listing.get("status") or "").lower()
    notes_val = (listing.get("notes") or "").lower()
    return (
        listing_status in ("open", "waitlist")
        or status_val == "open"
        or "accepting applications" in notes_val
        or "waitlist open" in notes_val
    )


def _listing_is_summary_candidate(listing: dict) -> bool:
    name = listing.get("property_name", "")
    name_lower = name.lower()
    nav_prefixes = [
        "quick links", "skip to", "home /", "your city /", "in this section",
        "select this as", "housing open side", "/ your city",
    ]
    if "closed" in name_lower:
        return False
    if any(name_lower.startswith(x) for x in nav_prefixes):
        return False
    is_structured = bool(listing.get("source") and ":" in str(listing.get("source", "")))
    return is_structured or len(name) > 4


def _format_run_status(run_stats: dict | None) -> str:
    if not run_stats:
        return ""

    attempted = int(run_stats.get("targets_attempted") or 0)
    failed = list(run_stats.get("failed_authorities") or [])
    succeeded = int(run_stats.get("targets_succeeded") or max(attempted - len(failed), 0))

    lines = ["## Run Status\n\n"]
    if failed:
        lines.append(
            f"- **Targets:** {succeeded} succeeded, {len(failed)} failed (of {attempted} attempted)\n"
        )
        lines.append(f"- **Failed targets:** {', '.join(failed)}\n")
        lines.append(
            "- Review `diff.csv` for `SCRAPE_FAILED` rows — scrape errors, not confirmed closures.\n"
        )
    else:
        lines.append(f"- **Targets:** {succeeded} succeeded (of {attempted} attempted)\n")
    lines.append("\n")
    return "".join(lines)


def generate_daily_summary(
    listings,
    skipped_targets=None,
    *,
    output_path=STAFF_DAILY_SUMMARY_PATH,
    run_stats=None,
):
    skipped_targets = skipped_targets or []
    with open(output_path, "w", encoding="utf-8") as f:
        f.write("# 🏠 Santa Clara County Housing Waitlist Summary\n")
        f.write(f"**Run:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n\n")
        f.write(_format_run_status(run_stats))

        seen = {}
        unique_opens = []

        for listing in listings:
            name_key = listing.get("property_name", "")[:55].lower().strip()
            key = (name_key, listing.get("authority"))

            if key in seen:
                continue
            seen[key] = True

            if _listing_is_open(listing) and _listing_is_summary_candidate(listing):
                unique_opens.append(listing)

        open_count = len(unique_opens)
        total_count = len(listings)
        f.write(f"**Records this run:** {total_count} extracted")
        if open_count:
            f.write(f" · {open_count} open or accepting applications\n\n")
        else:
            f.write("\n\n")

        if unique_opens:
            f.write("## 🔥 CURRENTLY OPEN / ACCEPTING APPLICATIONS\n\n")
            for listing in unique_opens[:10]:
                name = listing["property_name"][:85] + (
                    "..." if len(listing["property_name"]) > 85 else ""
                )
                f.write(f"**{name}**\n")
                f.write(f"Deadline: {listing.get('deadline') or 'None listed'}\n")
                f.write(f"Source: {listing['authority']}\n")
                link = (
                    listing.get("url")
                    or listing.get("source_url")
                    or listing.get("document_url")
                    or listing.get("flyer_url")
                    or ""
                )
                f.write(f"Link: {link}\n\n")
        elif total_count > 0:
            f.write(
                "**No open or accepting listings in this run.** "
                f"The {total_count} record(s) extracted are closed, waitlist-only, "
                "registration portals, or otherwise not currently accepting applications.\n\n"
            )
        else:
            f.write("**No listings extracted in this run.**\n\n")

        f.write("## 📊 Full Dataset for Import\n")
        f.write("- `current_full.csv` — full DB snapshot (all ever-seen rows; may exceed this run's count)\n")
        f.write("- `diff.csv` — this run's delta: NEW / UPDATED / STALE / SCRAPE_FAILED rows (use for incremental imports)\n")
        f.write("- `changelog_diffs.md` / `changelog_diffs.csv` — human/machine changelog vs last run\n\n")
        f.write("**Note:** Some city sites block automated access.\n")
        f.write("\nReady for internal tech mailing list.\n")

        # Human-readable report of intentionally skipped targets (never in CSV)
        if skipped_targets:
            f.write("\n## ⚠️  Intentionally Skipped Targets (no_public_list)\n\n")
            f.write("These targets are documented in TARGETS.md with the `no_public_list` marker.\n")
            f.write("They are skipped automatically to avoid wasting research effort on cities without\n")
            f.write("public structured BMR lists, waitlists, or extractable portals. When a usable public\n")
            f.write("source appears, a human removes the marker and the target becomes active again.\n\n")
            for auth, note in skipped_targets:
                f.write(f"- **{auth}**\n")
                if note:
                    f.write(f"  Notes: {note}\n")
                f.write("\n")

    print(f"✅ Generated clean, deduplicated {output_path}")
