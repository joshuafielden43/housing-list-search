# outputs.py
from datetime import datetime

def generate_daily_summary(listings):
    """Clean, actionable summary for the tech mailing list"""
    with open("daily_summary.md", "w", encoding="utf-8") as f:
        f.write(f"# 🏠 Santa Clara County Housing Waitlist Summary\n")
        f.write(f"**Run:** {datetime.now().strftime('%Y-%m-%d %H:%M')}\n")
        f.write(f"**Total listings extracted:** {len(listings)}\n\n")
        
        # Strong deduplication + filter out obvious closed/old stuff
        seen = {}
        unique_opens = []
        
        for l in listings:
            name = l.get("property_name", "")[:100].strip()
            key = (name.lower(), l.get("authority"))
            
            if key in seen:
                continue
            seen[key] = True
            
            # Only show truly "Open" items in the highlighted section
            if l.get("status") == "Open" and "closed" not in name.lower() and "until" not in l.get("notes", "").lower()[:200]:
                unique_opens.append(l)
        
        if unique_opens:
            f.write("## 🔥 CURRENTLY OPEN / ACCEPTING APPLICATIONS\n\n")
            for l in unique_opens[:12]:   # Keep it scannable
                name = l['property_name'][:90] + ("..." if len(l['property_name']) > 90 else "")
                f.write(f"**{name}**\n")
                f.write(f"Deadline: {l.get('deadline') or 'None listed'}\n")
                f.write(f"Source: {l['authority']}\n")
                f.write(f"Link: {l['url']}\n\n")
        else:
            f.write("**No currently open lists detected in this run.**\n\n")
        
        f.write("## 📊 Full Dataset for Import\n")
        f.write(f"- `current_full.csv` — {len(listings)} rows (ready for database import)\n")
        f.write("- `changelog_diffs.md` — changes since last run\n\n")
        f.write("**Note:** Some cities (Mountain View, Sunnyvale, etc.) block automated access. Manual spot-check recommended.\n")
        f.write("\nReady for internal tech mailing list.\n")
    
    print("✅ Generated clean, deduplicated daily_summary.md")
