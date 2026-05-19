# normalizer.py
import csv
from datetime import datetime

def normalize_listing(raw_data: dict) -> dict:
    """Core + flexible fields"""
    return {
        "source_authority": raw_data.get("authority", ""),
        "property_name": raw_data.get("property_name", ""),
        "url": raw_data.get("url", ""),
        "status": raw_data.get("status", "Unknown"),
        "deadline": raw_data.get("deadline", ""),
        "income_limits": raw_data.get("income_limits", ""),
        "unit_types": raw_data.get("unit_types", ""),
        "eligibility_flags": raw_data.get("eligibility_flags", []),
        "notes": raw_data.get("notes", ""),
        "scrape_date": datetime.now().isoformat(),
        "confidence": raw_data.get("confidence", 1.0)
    }

def save_current_full(listings: list):
    if not listings:
        print("⚠️ No listings to save")
        return
    
    fieldnames = [
        "source_authority", "property_name", "url", "status", "deadline",
        "income_limits", "unit_types", "eligibility_flags", "notes",
        "scrape_date", "confidence"
    ]
    
    with open("current_full.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in listings:
            row = normalize_listing(item)
            # Convert list to pipe-separated string for CSV
            row["eligibility_flags"] = "|".join(row["eligibility_flags"])
            writer.writerow(row)
    
    print(f"✅ Saved current_full.csv with {len(listings)} listings")
