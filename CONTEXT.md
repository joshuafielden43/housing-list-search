# CONTEXT.md — Domain Glossary

Ubiquitous language for housing-list-search. Architecture reviews and adapter work should use these terms at seams.

## Core concepts

| Term | Meaning |
|------|---------|
| **Target** | One row from `TARGETS.md` → SQLite `targets`: authority, URL, `scraping_measures`, administrator fields |
| **Listing** | One property or registration opportunity. Canonical shape via `listing_to_row()` in `listing.py` |
| **Run** | One `python main.py --run` invocation; identified by `run_id` (`YYYYMMDDTHHMMSS`) |
| **Measure** | Token in `scraping_measures` routing to an adapter (`bloom`, `housekeys`, `civicplus`, `waf_blocked`, …) |
| **DispatchRegistry** | `dispatch.py` — measures → adapter handlers; URL predicates → extraction handlers |
| **Adapter** | Platform-scoped scraper in `adapters/` or `extraction/` — named after vendor, never city |
| **marker fallback** | Optional `marker-pdf` path in `extraction/marker_pdf.py` when table/flyer/line extractors yield zero |

## Freshness & output

| Term | Meaning |
|------|---------|
| **STALE** | DB record not confirmed in current `run_id` |
| **Partial run** | `--target "City"` — scopes `diff.csv` STALE; preserves global `run_prev.csv` |
| **diff.csv** | DB-backed delta (`NEW` / `UPDATED` / `STALE` / `SCRAPE_FAILED`) |
| **current_full.csv** | Full `housing_records` export |

## Ethics & access

| Term | Meaning |
|------|---------|
| **polite_get** | Sole approved HTTP entry (`scraper.py`); robots.txt + delay |
| **no_public_list** | Intentional skip — no ethical public inventory |
| **waf_blocked** | Hard skip before network I/O |