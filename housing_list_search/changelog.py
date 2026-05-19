# changelog.py
from datetime import datetime

def generate_changelog(previous: list, current: list):
    """Generate changelog between runs (stub for v0.1)"""
    timestamp = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    content = f"""# Housing List Changelog
Run: {timestamp}
Previous listings: {len(previous)}
Current listings: {len(current)}

No previous data yet — this is the first run.
Full diff engine coming soon.
"""
    with open("changelog_diffs.md", "w", encoding="utf-8") as f:
        f.write(content)
    
    with open("changelog_diffs.csv", "w", encoding="utf-8") as f:
        f.write("change_type,authority,property_name,details,timestamp\n")
        f.write(f"INITIAL_RUN,All Targets,First Scrape,Initial population,{timestamp}\n")
    
    print("✅ Generated changelog_diffs.md and .csv")
