# outputs.py
from datetime import datetime

from housing_list_search.coverage import summarize_coverage

PARTIAL_DAILY_SUMMARY_PATH = "daily_summary_partial.md"
STAFF_DAILY_SUMMARY_PATH = "daily_summary.md"
OPEN_LISTING_DISPLAY_CAP = 100


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
        "quick links",
        "skip to",
        "home /",
        "your city /",
        "in this section",
        "select this as",
        "housing open side",
        "/ your city",
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


def _format_needs_review(run_stats: dict | None) -> str:
    if not run_stats:
        return ""

    suspicious = list(run_stats.get("suspicious_zero_authorities") or [])
    reverification = list(run_stats.get("reverification_due_authorities") or [])
    if not suspicious and not reverification:
        return ""

    lines = ["## Needs Review\n\n"]
    if suspicious:
        lines.append(
            f"- **Suspicious zero:** {len(suspicious)} property-inventory target(s) "
            "returned no property records this run\n"
        )
        lines.append(f"- **Authorities:** {', '.join(suspicious)}\n")
        lines.append(
            "- This is not a confirmed closure — the adapter may have broken, the source "
            "may have changed, or the inventory may genuinely be empty. Review the source "
            "and mark a Validated Zero in TARGETS.md when appropriate (ADR-0003).\n"
        )
    if reverification:
        lines.append(
            f"- **Reverification due:** {len(reverification)} Validated Zero(s) past "
            "review date in TARGETS.md\n"
        )
        lines.append(f"- **Authorities:** {', '.join(reverification)}\n")
        lines.append(
            "- Re-confirm the source is still empty, update the Validated Zero dates, "
            "or remove the metadata if inventory has returned.\n"
        )
    lines.append("\n")
    return "".join(lines)


def _format_integrity_summary(run_stats: dict | None) -> str:
    if not run_stats:
        return ""

    stale_n = int(run_stats.get("stale_n") or 0)
    scrape_failed_n = int(run_stats.get("scrape_failed_n") or 0)
    if not stale_n and not scrape_failed_n:
        return ""

    lines = ["## Integrity signals (diff.csv)\n\n"]
    if stale_n:
        lines.append(f"- **STALE:** {stale_n} record(s) not confirmed this run\n")
    if scrape_failed_n:
        lines.append(
            f"- **SCRAPE_FAILED:** {scrape_failed_n} record(s) from failed authority scrapes "
            "(not confirmed closures)\n"
        )
    if stale_n >= int(run_stats.get("stale_warn_threshold") or 5):
        lines.append(
            "- Review `diff.csv`, then prune when appropriate: "
            "`python scripts/db_manage.py prune --not-seen-since 45`\n"
        )
    lines.append("\n")
    return "".join(lines)


def _format_coverage_summary(listings) -> str:
    cov = summarize_coverage(listings)
    if cov.total == 0:
        return ""

    lines = ["## Coverage breakdown\n\n"]
    lines.append(
        f"- **Property inventory:** {cov.property_count} (per-property or per-unit records)\n"
    )
    if cov.portal_count:
        lines.append(
            f"- **Portal pointers:** {cov.portal_count} "
            f"(registration/notification entry points — not unit lists)\n"
        )
    if cov.program_count:
        lines.append(
            f"- **Program extracts:** {cov.program_count} "
            f"(program-level PDF/page text — not named properties)\n"
        )
    lines.append(
        f"- **UEO-style property count:** {cov.property_count} "
        f"(excludes portals and program noise)\n\n"
    )

    if cov.portal_records:
        lines.append("### Portal pointers (not property inventory)\n\n")
        for rec in cov.portal_records:
            auth = rec.get("authority") or "Unknown"
            link = rec.get("url") or rec.get("administrator_url") or rec.get("source_url") or ""
            lines.append(f"- **{auth}** — register via HouseKeys")
            if link:
                lines.append(f" ({link})")
            lines.append("\n")
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
        f.write(_format_needs_review(run_stats))
        f.write(_format_integrity_summary(run_stats))
        f.write(_format_coverage_summary(listings))

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
        cov = summarize_coverage(listings)
        f.write(
            f"**Records this run:** {cov.total} extracted "
            f"({cov.property_count} property inventory"
        )
        if cov.portal_count:
            f.write(f", {cov.portal_count} portal pointer{'s' if cov.portal_count != 1 else ''}")
        if cov.program_count:
            f.write(f", {cov.program_count} program extract{'s' if cov.program_count != 1 else ''}")
        f.write(")")
        if open_count:
            f.write(f" · {open_count} open or accepting applications\n\n")
        else:
            f.write("\n\n")

        if unique_opens:
            f.write("## 🔥 CURRENTLY OPEN / ACCEPTING APPLICATIONS\n\n")
            display = unique_opens[:OPEN_LISTING_DISPLAY_CAP]
            for listing in display:
                name = listing["property_name"][:85] + (
                    "..." if len(listing["property_name"]) > 85 else ""
                )
                f.write(f"**{name}**\n")
                f.write(f"Deadline: {listing.get('deadline') or 'None listed'}\n")
                addr = listing.get("address") or ""
                if addr:
                    f.write(f"Address: {addr}\n")
                br = listing.get("unit_types") or listing.get("bedrooms") or ""
                if br:
                    f.write(f"Units/BR: {br}\n")
                status = listing.get("status") or ""
                if status:
                    f.write(f"Status: {status}\n")
                f.write(f"Source: {listing['authority']}\n")
                link = (
                    listing.get("url")
                    or listing.get("source_url")
                    or listing.get("document_url")
                    or listing.get("flyer_url")
                    or ""
                )
                f.write(f"Link: {link}\n\n")
            if open_count > len(display):
                remaining = open_count - len(display)
                f.write(
                    f"_+ {remaining} more open listing(s) in this run — "
                    "filter `current_full.csv` for open/accepting status._\n\n"
                )
        elif cov.total > 0:
            f.write(
                "**No open or accepting listings in this run.** "
                f"The {cov.total} record(s) extracted are closed, waitlist-only, "
                "registration portals, or otherwise not currently accepting applications.\n\n"
            )
        else:
            f.write("**No listings extracted in this run.**\n\n")

        f.write("## 📊 Full Dataset for Import\n")
        f.write(
            "- `current_full.csv` — full DB snapshot (all ever-seen rows; may exceed this run's count)\n"
        )
        f.write(
            "- `diff.csv` — this run's delta: NEW / UPDATED / STALE / SCRAPE_FAILED rows (use for incremental imports)\n"
        )
        f.write(
            "- `changelog_diffs.md` / `changelog_diffs.csv` — human/machine changelog vs last run\n\n"
        )
        f.write("**Note:** Some city sites block automated access.\n")
        f.write("\nReady for internal tech mailing list.\n")

        # Human-readable report of intentionally skipped targets (never in CSV)
        if skipped_targets:
            f.write("\n## ⚠️  Intentionally Skipped Targets (no_public_list)\n\n")
            f.write(
                "These targets are documented in TARGETS.md with the `no_public_list` marker.\n"
            )
            f.write(
                "They are skipped automatically to avoid wasting research effort on cities without\n"
            )
            f.write(
                "public structured BMR lists, waitlists, or extractable portals. When a usable public\n"
            )
            f.write(
                "source appears, a human removes the marker and the target becomes active again.\n\n"
            )
            for auth, note in skipped_targets:
                f.write(f"- **{auth}**\n")
                if note:
                    f.write(f"  Notes: {note}\n")
                f.write("\n")

    print(f"✅ Generated clean, deduplicated {output_path}")
