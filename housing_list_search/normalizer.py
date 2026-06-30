# normalizer.py
import csv
from datetime import datetime

from housing_list_search.csv_safety import sanitize_csv_row
from housing_list_search.status_labels import resolve_status_label


def normalize_listing(raw_data: dict) -> dict:
    """Core + flexible fields. Now preserves rich fields from new HousingRecord extractors."""
    notes = raw_data.get("notes", "")
    # If we have separate rich fields, surface the best contact/apply info into notes
    extra = []
    if raw_data.get("address") and raw_data.get("address") not in notes:
        extra.append(f"addr: {raw_data['address']}")
    if raw_data.get("phone"):
        extra.append(f"phone: {raw_data['phone']}")
    if raw_data.get("email"):
        extra.append(f"email: {raw_data['email']}")
    if raw_data.get("bedrooms"):
        extra.append(f"br: {raw_data['bedrooms']}")
    if extra:
        notes = (notes + " | " + " | ".join(extra)).strip(" |")

    now = datetime.now().isoformat()

    return {
        "source_authority": raw_data.get("authority", ""),
        "property_name": raw_data.get("property_name", ""),
        "url": raw_data.get("url") or raw_data.get("document_url", ""),
        "address": raw_data.get("address", ""),
        "phone": raw_data.get("phone", ""),
        "email": raw_data.get("email", ""),
        "bedrooms": raw_data.get("bedrooms", ""),
        "status": resolve_status_label(raw_data),
        "listing_status": (raw_data.get("listing_status") or "").lower(),
        "deadline": raw_data.get("deadline", ""),
        "income_limits": raw_data.get("income_limits", ""),
        "unit_types": raw_data.get("bedrooms") or raw_data.get("unit_types", ""),
        "eligibility_flags": raw_data.get("eligibility_flags", []),
        "notes": notes,
        "scrape_date": now,
        "confidence": raw_data.get("confidence", 1.0),
        # Support for delegated administrators (e.g. Rise Housing for Cupertino BMR)
        "administrator": raw_data.get("administrator", ""),
        "administrator_url": raw_data.get("administrator_url", ""),
        "administrator_phone": raw_data.get("administrator_phone", ""),
        "administrator_contact": raw_data.get("administrator_contact", ""),
        # Freshness / delta metadata (0.8.2+)
        "last_seen": raw_data.get("last_seen") or now,
        "first_seen": raw_data.get("first_seen") or now,
        "source": raw_data.get("source", ""),
        "source_url": raw_data.get("source_url") or raw_data.get("document_url", ""),
        "expires_at": raw_data.get("expires_at", ""),
    }

def save_current_full(listings: list):
    """Write listings directly to CSV. Production --run uses db.export_csv() instead."""
    if not listings:
        print("⚠️ No listings to save")
        return
    
    fieldnames = [
        "source_authority", "property_name", "address", "phone", "email",
        "bedrooms", "url", "status", "listing_status", "deadline",
        "income_limits", "unit_types", "eligibility_flags", "notes",
        "scrape_date", "confidence",
        "administrator", "administrator_url", "administrator_phone", "administrator_contact",
        # Freshness fields (0.8.2+)
        "last_seen", "first_seen", "source", "source_url", "expires_at"
    ]
    
    with open("current_full.csv", "w", newline="", encoding="utf-8") as f:
        writer = csv.DictWriter(f, fieldnames=fieldnames)
        writer.writeheader()
        for item in listings:
            row = normalize_listing(item)
            # Coerce eligibility_flags to list before joining (scrapers may hand a string)
            flags = row["eligibility_flags"]
            if isinstance(flags, str):
                flags = [flags] if flags else []
            row["eligibility_flags"] = "|".join(flags)
            writer.writerow(sanitize_csv_row(row))
    
    print(f"✅ Saved current_full.csv with {len(listings)} listings")
