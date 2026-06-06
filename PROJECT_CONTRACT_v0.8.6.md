# Housing List Aggregator — Living Contract (v0.8.6)

**Status:** Active (2026-06-05)  
**Supersedes:** `PROJECT_CONTRACT_v0.8.2.md` (archived)

This is the short, current contract. Detail lives in `AGENTS.md`, `SOUL.md`, and `TARGETS.md`.

---

## Daily run (`python main.py --run`)

1. `registry.load_targets_to_db()` — TARGETS.md → `targets` table (registry owns this schema)
2. `runner.run_target()` per active row — measure-driven adapter dispatch
3. `deduplicate_listings()` — cross-source dedupe
4. `db.upsert_listings()` — listings → `housing_records` (db.py owns this schema)
5. Export `current_full.csv` (full DB) and `diff.csv` (NEW/UPDATED/STALE for this `run_id`)
6. Warn when `STALE` count ≥ 5 (configurable constant in `db.py`)
7. `changelog` (vs `run_prev.csv`) + `daily_summary.md`

**Prune is manual:** `python scripts/db_manage.py prune --not-seen-since 45`

---

## Responsibility split

| Concern | Owner |
|---|---|
| Target list curation | Human + `TARGETS.md` |
| Target DB ingest | `registry.py` → `targets` table |
| Listing persistence | `db.py` → `housing_records` table |
| Adapter routing | `runner.py` (by `scraping_measures`) |
| Diagnostics | `scripts/doctor.py` |
| Destructive DB ops | `scripts/db_manage.py` |

---

## Output files

| File | Use when |
|---|---|
| `current_full.csv` | Full known inventory |
| `diff.csv` | Incremental import / what changed this run |
| `changelog_diffs.*` | Human audit trail vs last scrape |
| `daily_summary.md` | Staff mailing list |

See `AGENTS.md` § Output files for semantics of `STALE` vs changelog `REMOVED`.

---

## Tests

- **CI:** `pytest tests/ -m "not integration"` (unit + contract tests)
- **Opt-in:** `pytest tests/ -m integration` (live portals)