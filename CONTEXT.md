# CONTEXT.md — Domain Glossary

Ubiquitous language for housing-list-search. Architecture reviews and adapter work should use these terms at seams.

**Task tracking:** Vikunja project **#9** (see `.agents/MEMORY.md`).

## Core concepts

| Term | Meaning |
|------|---------|
| **Target** | One row from `TARGETS.md` → SQLite `targets`: authority, URL, `scraping_measures`, administrator fields |
| **Schema** | `schema.py` — sole DDL owner for `housing_registry.db`; `registry.py` ingests targets, `db.py` persists listings |
| **Listing** | One property or registration opportunity. `canonicalize_listings()` applies `listing_to_row()` before dedupe; empty URLs get `hls:` surrogate keys via listing module (persistence_url logic) |
| **Run** | One `python main.py --run` invocation; identified by `run_id` (`YYYYMMDDTHHMMSS`) |
| **RunPipeline** | `pipeline.py` — collect → persist → publish; `cli.py` delegates here |
| **Staff Publish** | `staff_publish.py` — post-persist staff artifact policy: partial stubs, run_prev baseline, changelog, daily_summary, RUN_EVENT, Needs Review surface |
| **Measure** | Token in `scraping_measures` routing to an adapter (`bloom`, `housekeys`, `civicplus`, `waf_blocked`, …) |
| **DispatchRegistry** | `dispatch.py` — measures → adapter handlers; URL predicates → extraction handlers |
| **Adapter** | Platform-scoped scraper in `adapters/` or `extraction/` — named after vendor, never city |
| **PDF default stack** | pdfplumber for tables, text, and flyer heuristics (ADR-0005); no PyMuPDF in default install |
| **marker fallback** | Optional GPL `marker-pdf` path in `extraction/marker_pdf.py` (`requirements-ocr.txt`) when pdfplumber paths yield zero |

## Freshness & output

| Term | Meaning |
|------|---------|
| **STALE** | DB record not confirmed in current `run_id` |
| **Cross-source mirror confirm** | When dedupe keeps one survivor for the same physical property across authorities, other identities still seen this run get `last_run_id` touched (`confirm_listing_identities`) so they are not false-STALE (#661 / #773) |
| **SCRAPE_FAILED** | DB record not confirmed because the authority scrape failed in current `run_id`; not evidence of closure or removal |
| **REMOVED** | Staff-facing changelog event for a record absent after a successful scrape of its authority; do not emit for failed authorities |
| **Disappearance semantics** | How the system explains records absent from this run. `diff.csv` is the source of truth: staff-facing outputs project these labels rather than deriving closure/removal independently |
| **Partial run** | `--target "City"` — scopes `diff.csv` STALE; preserves global `run_prev.csv` |
| **diff.csv** | Machine delta (`NEW` / `UPDATED` / `STALE` / `SCRAPE_FAILED`); labels from pure `classify_machine_change` |
| **Disappearance** | `disappearance.py` — deep module: machine Diff labels + staff projection (ADDED/REMOVED/STALE/SCRAPE_FAILED/STATUS_CHANGE) from `diff.csv` (ADR-0001). `run_prev.csv` only for STATUS_CHANGE. `freshness.py` is a compat shim. |
| **Freshness** | Legacy name for change semantics; prefer **Disappearance**. Identity remains `(authority, property_name, url)` via `listing.listing_identity` |
| **Coverage** | `coverage.py` — `record_kind`: `property` / `portal` / `program`; UEO-style count excludes portals |
| **current_full.csv** | Full `housing_records` export |

## Operational review

| Term | Meaning |
|------|---------|
| **Suspicious Zero** | A zero-record result from an authority or adapter that normally represents property inventory; it requires human attention unless already covered by a current validation |
| **Validated Zero** | A zero-record authority state that a person has confirmed as real for a dated review window |
| **Needs Review** | A run or authority state that should be surfaced to an operator without treating otherwise confirmed records as unusable |
| **RunReview** | `needs_review.py` deep spine: `assess_collect_review` → `build_run_review` → `surface_run_review` (log / webhook / Vikunja). Composes Suspicious Zero, Validated Zero due, low-yield, STALE, SCRAPE_FAILED. ADR-0004: never fails the Run |
| **Reverification Task** | Vikunja task `[Reverify] {authority}` — auto-created/updated when suspicious zero or reverification due fires (`vikunja_reverification.py`); human closes after TARGETS.md update |

## Ethics & access

| Term | Meaning |
|------|---------|
| **Access** | `access.py` — sole outbound seam (HTTP + browser): `polite_get` / `polite_post` / `browser_page` / `safe_goto`; policy, robots, throttle. Implementation: `scraper.py`, `playwright_nav.py` (private) |
| **polite_get** | Approved HTTP fetch via Access; robots.txt + delay |
| **no_public_list** | Intentional skip — no ethical public inventory |
| **waf_blocked** | Hard skip before network I/O |
