# normalizer.py
import csv

from housing_list_search.csv_safety import sanitize_csv_row
from housing_list_search.listing import listing_to_row


def normalize_listing(raw_data: dict) -> dict:
    """Core + flexible fields. Delegates to listing_to_row for canonical shape."""
    row = listing_to_row(raw_data)
    # CSV schema uses source_authority; DB uses authority — same value, different column name.
    return {
        "source_authority": row["authority"],
        "property_name": row["property_name"],
        "url": row["url"],
        "address": row["address"],
        "phone": row["phone"],
        "email": row["email"],
        "bedrooms": row["bedrooms"],
        "status": row["status"],
        "listing_status": row["listing_status"],
        "deadline": row["deadline"],
        "income_limits": row["income_limits"],
        "unit_types": row["unit_types"],
        "eligibility_flags": row["eligibility_flags"],
        "notes": row["notes"],
        "scrape_date": row["scrape_date"],
        "confidence": row["confidence"],
        "administrator": row["administrator"],
        "administrator_url": row["administrator_url"],
        "administrator_phone": row["administrator_phone"],
        "administrator_contact": row["administrator_contact"],
        "last_seen": row["last_seen"],
        "first_seen": row["first_seen"],
        "source": row["source"],
        "source_url": row["source_url"],
        "expires_at": row["expires_at"],
    }


def save_current_full(listings: list):
    """Write listings directly to CSV. Production --run uses db.export_csv() instead."""
    if not listings:
        print("⚠️ No listings to save")
        return

    fieldnames = [
        "source_authority",
        "property_name",
        "address",
        "phone",
        "email",
        "bedrooms",
        "url",
        "status",
        "listing_status",
        "deadline",
        "income_limits",
        "unit_types",
        "eligibility_flags",
        "notes",
        "scrape_date",
        "confidence",
        "administrator",
        "administrator_url",
        "administrator_phone",
        "administrator_contact",
        "last_seen",
        "first_seen",
        "source",
        "source_url",
        "expires_at",
    ]

    with open("current_full.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in listings:
            writer.writerow(sanitize_csv_row(normalize_listing(item)))

    print(f"✅ Saved current_full.csv with {len(listings)} listings")
