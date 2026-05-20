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

            # Very strict filter
            if (l.get("status") == "Open"
                and "closed" not in name_lower
                and len(name) > 22
                and name.count(" ") >= 4
                and not any(name_lower.startswith(x) for x in 
                    ["quick links", "skip to", "home /", "your city /", "in this section", 
                     "select this as", "housing open side", "/ your city"])):

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
        f.write(f"- `current_full.csv` — {len(listings)} rows (ready for database import)\n")
        f.write("- `changelog_diffs.md` — changes since last run\n\n")
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
