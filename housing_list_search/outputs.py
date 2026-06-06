# outputs.py
from datetime import datetime

def generate_daily_summary(listings, skipped_targets=None):
    skipped_targets = skipped_targets or []
    with open("daily_summary.md", "w", encoding="utf-8") as f:
        f.write(f"# 🏠 Santa Clara County Housing Waitlist Summary\n")
        f.write(f"**Run:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"**Total listings extracted:** {len(listings)}\n\n")

        seen = {}
        unique_opens = []

        for l in listings:
            name_key = l.get("property_name", "")[:55].lower().strip()
            key = (name_key, l.get("authority"))

            if key in seen:
                continue
            seen[key] = True

            name = l.get("property_name", "")
            name_lower = name.lower()

            # Determine whether this record represents an open/accepting listing.
            # Priority order:
            #   1. listing_status field (set by Bloom extractor: "open", "waitlist",
            #      "closed", "coming_soon") — reliable, no string fragility.
            #   2. status field (generic scrapers set "Open" / "Unknown").
            #   3. Notes string fallback for records produced before listing_status
            #      was added (backwards compat).
            listing_status = (l.get("listing_status") or "").lower()
            status_val = (l.get("status") or "").lower()
            notes_val = (l.get("notes") or "").lower()
            is_open = (
                listing_status in ("open", "waitlist")
                or status_val == "open"
                or "accepting applications" in notes_val
                or "waitlist open" in notes_val
            )

            nav_prefixes = [
                "quick links", "skip to", "home /", "your city /", "in this section",
                "select this as", "housing open side", "/ your city",
            ]

            # Hard exclusions apply to all records regardless of source.
            if "closed" in name_lower:
                continue
            if any(name_lower.startswith(x) for x in nav_prefixes):
                continue

            # Length/word-count heuristics only apply to generic-scraper records.
            # Structured records (bloom:*, housekeys:*, gis:*, etc.) have already
            # been filtered at extraction time — a short real name like "Monroe Commons"
            # should never be blocked by the noise filter.
            is_structured = bool(l.get("source") and ":" in str(l.get("source", "")))
            name_is_real = is_structured or (len(name) > 4)

            if is_open and name_is_real:

                unique_opens.append(l)

        if unique_opens:
            f.write("## 🔥 CURRENTLY OPEN / ACCEPTING APPLICATIONS\n\n")
            for l in unique_opens[:10]:
                name = l['property_name'][:85] + ("..." if len(l['property_name']) > 85 else "")
                f.write(f"**{name}**\n")
                f.write(f"Deadline: {l.get('deadline') or 'None listed'}\n")
                f.write(f"Source: {l['authority']}\n")
                f.write(f"Link: {l['url']}\n\n")
        else:
            f.write("**No currently open lists detected in this run.**\n\n")

        f.write("## 📊 Full Dataset for Import\n")
        f.write(f"- `current_full.csv` — full DB snapshot (all ever-seen rows; may exceed this run's count)\n")
        f.write(f"- `diff.csv` — this run's delta: NEW / UPDATED / STALE rows (use for incremental imports)\n")
        f.write("- `changelog_diffs.md` / `changelog_diffs.csv` — human/machine changelog vs last run\n\n")
        f.write(f"This run produced **{len(listings)} listings** after deduplication.\n\n")
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

    print("✅ Generated clean, deduplicated daily_summary.md")
