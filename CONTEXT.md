# CONTEXT.md — Domain Glossary

Ubiquitous language for housing-list-search. Architecture reviews and adapter work should use these terms at seams.

**Task tracking:** Vikunja project **#9** (see `.agents/MEMORY.md`).

## Core concepts

| Term | Meaning |
|------|---------|
| **Target** | One row from `TARGETS.md` → SQLite `targets`: authority, URL, `scraping_measures`, administrator fields |
| **Schema** | `schema.py` — sole DDL owner for `housing_registry.db`; `registry.py` ingests targets |
| **Inventory Store** | `inventory_store.py` — Run-path SQLite: upsert, identity confirm touches, machine CSV export, full-run log. Prefer this type on Machine Persist / Staff Publish (#1072) |
| **Operator Maintenance** | `operator_maintenance.py` — prune, snapshot, drop, info. Operator CLI only. `db.DatabaseManager` is a thin facade exposing both Store + Maintenance |
| **Listing** | One property or registration opportunity. `canonicalize_listings()` applies `listing_to_row()` before dedupe; empty URLs get `hls:` surrogate keys via listing module (persistence_url logic) |
| **Run** | One `python main.py --run` invocation; identified by `run_id` (`YYYYMMDDTHHMMSS`) |
| **RunPipeline** | `pipeline.py` — collect → Machine Persist → Staff Publish; `cli.py` delegates here |
| **Machine Persist** | `machine_persist.py` — post-collect machine path: canonicalize → dedupe (+ mirror confirm set) → upsert → machine CSVs (`current_full` / `diff`) → STALE/SCRAPE_FAILED thresholds (`persist_run`) |
| **Staff Publish** | `staff_publish.py` — post-persist staff artifact *policy*: partial vs full, run_prev baseline, changelog, RUN_EVENT, Needs Review surface |
| **Staff Summary** | `staff_summary.py` — staff markdown *bodies*: daily_summary (open vs waitlist enrollment, contacts), proposed_prune. Interface: `render_staff_summary` |
| **Measure** | Token in `scraping_measures` routing to an adapter (`bloom`, `housekeys`, `civicplus`, `waf_blocked`, …) |
| **DispatchRegistry** | `dispatch.py` — measures → adapter handlers; URL predicates → extraction handlers |
| **Adapter** | Platform-scoped scraper in `adapters/` or `extraction/` — named after vendor, never city |
| **PDF default stack** | pdfplumber for tables, text, and flyer heuristics (ADR-0005); no PyMuPDF in default install |
| **marker fallback** | Optional OCR tier in `extraction/marker_pdf.py` (`requirements-ocr.txt`) when pdfplumber yields zero. **Code GPL-3.0 + weights OpenRAIL-M** (not MIT) — operator obligations in ADR-0005 / #778 |

## Freshness & output

| Term | Meaning |
|------|---------|
| **STALE** | DB record not confirmed in current `run_id` |
| **Cross-source mirror confirm** | When dedupe keeps one survivor for the same physical property across authorities, other identities still seen this run get `last_run_id` touched (`confirm_listing_identities`) so they are not false-STALE (#661 / #773 / #1071). `deduplicate_for_run` returns `DedupeResult(survivors, mirrors_to_confirm)`; Machine Persist applies confirm — callers do not re-derive the set |
| **SCRAPE_FAILED** | DB record not confirmed because the authority scrape failed in current `run_id`; not evidence of closure or removal |
| **Pagination cap** | Safety max page count on multi-page inventory adapters. Hitting the cap with a full final page is incomplete inventory → `SourceFetchError` / SCRAPE_FAILED, not silent truncate (#776). Shared walk: `inventory_pagination.walk_paginated_inventory` (#1074) — MidPen, jsco.net, ArcGIS |
| **REMOVED** | Staff-facing changelog event for a record absent after a successful scrape of its authority; do not emit for failed authorities |
| **Disappearance semantics** | How the system explains records absent from this run. `diff.csv` is the source of truth: staff-facing outputs project these labels rather than deriving closure/removal independently |
| **Partial run** | `--target "City"` — writes `diff_partial.csv` / `current_full_partial.csv`; preserves global `diff.csv`, `current_full.csv`, `run_prev.csv` (#241) |
| **diff.csv** | Machine delta (`NEW` / `UPDATED` / `STALE` / `SCRAPE_FAILED`); labels from pure `classify_machine_change` |
| **Disappearance** | `disappearance.py` — deep module: machine Diff labels + staff projection (ADDED/REMOVED/STALE/SCRAPE_FAILED/STATUS_CHANGE) from `diff.csv` (ADR-0001). `run_prev.csv` only for STATUS_CHANGE. There is no `freshness.py` — do not re-add a re-export shim under that name |
| **Freshness** | Ordinary English for “is inventory current?” (e.g. STALE prune, daily run). Not a module. Change semantics live under **Disappearance**. |
| **Listing Identity** | `listing_identity.py` — persistence key `(authority, property_name, url)`, cross-source merge key, mirror confirm keys, and pure `alias_matches` for Store touch. Does not classify STALE/REMOVED (Disappearance) and does not execute SQL (Store only). |
| **Coverage** | `coverage.py` — `record_kind`: `property` / `portal` / `program`; UEO-style count excludes portals |
| **current_full.csv** | Full `housing_records` export |

## Operational review

| Term | Meaning |
|------|---------|
| **Suspicious Zero** | A zero-record result from an authority or adapter that normally represents property inventory; it requires human attention unless already covered by a current validation |
| **Validated Zero** | A zero-record authority state that a person has confirmed as real for a dated review window |
| **Needs Review** | A run or authority state that should be surfaced to an operator without treating otherwise confirmed records as unusable |
| **RunReview** | `needs_review.py` deep spine: `assess_collect_review` → `build_run_review` → `surface_run_review` (log / webhook / Vikunja). Composes Suspicious Zero, Validated Zero due, **low-yield** (pages Needs Review; measure portfolio floors), STALE, SCRAPE_FAILED. ADR-0004: never fails the Run |
| **Low-yield** | Inventory target with 0 < property_count < max(global threshold, measure floor). Surfaces via Needs Review (#1083) — possible silent partial scrape, not a successful thin inventory |
| **Reverification Task** | Vikunja task `[Reverify] {authority}` — auto-created/updated when suspicious zero or reverification due fires (`vikunja_reverification.py`); human closes after TARGETS.md update |

## Ethics & access

| Term | Meaning |
|------|---------|
| **Access** | `access.py` — sole outbound seam (HTTP + browser): `polite_get` / `polite_post` / `browser_page` / `safe_goto`; policy, robots, throttle. Implementation: `scraper.py`, `playwright_nav.py` (private). Callers and public tests import Access only (#1073) |
| **Machine Persist canonicalize** | `persist_run` owns `canonicalize_listings` once; `upsert_listings(..., canonicalize=False)` on the Run path so the Store does not re-own Listing shape |
| **Playwright egress policy** | Every `browser_page` installs a route filter (#775 / #1082): navigations + data-carrying types (document/xhr/fetch/script) use DNS-resolved URL policy; static assets host/IP-only. Response spies (Bloom) DNS-check response URLs |
| **polite_get** | Approved HTTP fetch via Access; robots.txt + delay |
| **no_public_list** | Intentional skip — no ethical public inventory |
| **waf_blocked** | Hard skip before network I/O |
