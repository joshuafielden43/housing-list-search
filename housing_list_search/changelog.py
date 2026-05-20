# changelog.py
from datetime import datetime

def generate_changelog(previous: list, current: list, skipped_targets=None):
    """Generate changelog between runs (stub for v0.1).
    Also documents intentionally skipped targets (no_public_list) in the human-readable log.
    """
    skipped_targets = skipped_targets or []
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")

    content = f"""# Housing List Changelog
Run: {timestamp}
Previous listings: {len(previous)}
Current listings: {len(current)}
"""

    if skipped_targets:
        content += "\n## ⚠️ Intentionally Skipped Targets (no_public_list)\n\n"
        content += "These targets were skipped because they are marked in TARGETS.md as having no public\n"
        content += "structured BMR list or extractable portal. They will not be researched again until the\n"
        content += "marker is manually removed by a human.\n\n"
        for auth, note in skipped_targets:
            content += f"- {auth}"
            if note:
                content += f" — {note}"
            content += "\n"
        content += "\n"

    content += """
No previous data yet — this is the first run.
Full diff engine coming soon.
"""
    with open("changelog_diffs.md", "w", encoding="utf-8") as f:
        f.write(content)
    
    with open("changelog_diffs.csv", "w", encoding="utf-8") as f:
        f.write("change_type,authority,property_name,details,timestamp\n")
        f.write(f"INITIAL_RUN,All Targets,First Scrape,Initial population,{timestamp}\n")
        for auth, _ in skipped_targets:
            f.write(f"SKIPPED,no_public_list,{auth},marked in TARGETS.md,{timestamp}\n")
    
    print("✅ Generated changelog_diffs.md and .csv")
