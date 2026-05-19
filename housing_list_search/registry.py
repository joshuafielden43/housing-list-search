# registry.py
import sqlite3
import csv
from datetime import datetime
import os

DB_PATH = "housing_registry.db"

def init_db():
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    c.execute('''CREATE TABLE IF NOT EXISTS targets (
        id INTEGER PRIMARY KEY,
        authority TEXT,
        url TEXT,
        notes TEXT,
        scraping_measures TEXT,
        priority TEXT,
        last_seen TEXT,
        last_successful_scrape TEXT,
        confidence_score REAL DEFAULT 0.0
    )''')
    conn.commit()
    conn.close()
    print("✅ SQLite registry initialized")

def load_targets_to_db():
    init_db()
    conn = sqlite3.connect(DB_PATH)
    c = conn.cursor()
    
    # Clear and reload from markdown
    c.execute("DELETE FROM targets")
    
    with open("TARGETS.md", "r", encoding="utf-8") as f:
        lines = f.readlines()
    
    in_table = False
    for line in lines:
        if line.strip().startswith("City/Authority"):
            in_table = True
            continue
        if in_table and "|" in line and not line.startswith("---"):
            parts = [p.strip() for p in line.split("|")]
            if len(parts) >= 6:
                authority = parts[0]
                url = parts[1]
                notes = parts[2]
                measures = parts[3]
                priority = parts[4]
                last_seen = parts[5]
                
                c.execute("""INSERT INTO targets 
                    (authority, url, notes, scraping_measures, priority, last_seen)
                    VALUES (?, ?, ?, ?, ?, ?)""",
                    (authority, url, notes, measures, priority, last_seen))
    
    conn.commit()
    conn.close()
    print(f"✅ Loaded targets into SQLite registry ({DB_PATH})")
