# Housing List Aggregator — Project Contract (v0.8.2) [ARCHIVED]

> **Archived as of v0.8.5.** This document describes the v0.8.2 deliverables and is kept for historical reference only.
> The current product state is described in `PROJECT_CONTRACT_v0.8.6.md`, README.md, AGENTS.md, and SOUL.md.
> Key divergences from this contract: six adapters (not five — bloom_housing added), san_jose.py replaced by bloom_housing.py, listing_status field added to every Bloom record, freshness fields now set by Bloom adapter, changelog stub replaced with real diff engine.

**Note:** Filename is now versioned to match the release (iterated via `git mv`). Content is the authoritative contract for v0.8.2.

**Project Name:** Housing List Aggregator (Santa Clara County + Portable Skill)  
**Status:** Alpha (First-class Recurring Administrators + Freshness Schema + Reliable Extraction Spine)  
**Date:** 2026-05-18  
**Last Updated:** 2026-05-20 (v0.8.2: cdn + alta first-class adapters, full freshness metadata on every record, extraction/ layer, Housing Group as recurring administrator, registry nanny + doctor --fix, operational no_public_list, quiet logs)  
**Owner:** Joshua Fielden (for local Santa Clara County nonprofits)  
**Goal:** Create a modular, reusable "skill" that helps nonprofits deal with fragmented city-by-city housing waitlists.

---

## 1. Original Vision (Non-Negotiable)

We are building this to help **nonprofits in Santa Clara County** (and eventually other counties) solve a real operational pain:

> Every city in the county maintains its own housing list / waitlist / BMR / lottery system for low-income and unhoused individuals. There is no single source of truth.

Key requirements from the founding vision:
- The **list of targets** must be independent of the scraping engine (easy to edit/share as `TARGETS.md`).
- Turn this into a **reusable skill** that an autonomous agent (especially Hermes) can use.
- Make it **modular and cheap to replicate** for other nonprofits or other counties.
- Should be portable to other frameworks (cowork, openclaw, codex, etc.) by reading metadata + engine.
- Focus first on **Santa Clara County**, but design for portability.

**Success looks like:** A nonprofit data/tech team can run this daily, get actionable open waitlists/lotteries, and import clean data into their systems with minimal maintenance.

---

## 2. Current Built State (What We Actually Have)

As of v0.8.2 we have:

**Core Components:**
- `TARGETS.md` as the human-editable source of truth (20 rows: 15 Santa Clara cities + SCCHA variants + 2 San Mateo Housing Group cities + Palo Alto)
- SQLite registry + nanny layer (`sanitize_target`, `load_targets_to_db`, `get_active_targets` / `get_skipped_targets`)
- Five first-class, company/tool-named adapters (never city-named):
  - `john_stewart.py` — consolidated vendor + custom front-end properties
  - `gis_extraction.py` — municipal GIS + federated managers (Cupertino + Rise Housing reference)
  - `housekeys.py` — delegated registration / notification / lottery portals (Milpitas reference)
  - `cdn.py` — CDN/WAF-protected document viewers (DocumentCenter, showdocument, docaccess) — primary path for Housing Group cities (Campbell, Los Altos, Menlo Park, Half Moon Bay)
  - `alta.py` — Alta Housing delegated administrator (Palo Alto reference)
- Preferred high-quality `extraction/` layer (`extract_target`) with San José portal + PDF support
- Full freshness / delta foundation on every record (`last_seen`, `first_seen`, `source`, `source_url`, `expires_at`)
- `scripts/doctor.py --fix` — canonical command for full re-ingest + re-sanitization from TARGETS.md
- Operational `no_public_list` handling (WARN logs + human sections in daily_summary/changelog; never pollutes CSV)
- `current_full.csv` + `daily_summary.md` + `changelog_diffs.*` outputs with deduplication
- Strong logging discipline (adapter-prefixed messages, GIS noise moved to debug)
- Explicit Scope & Guardrails + Known Low-Value Patterns + PATTERN FOR NEW USE CASES in every adapter + AGENTS.md

**Key Achievements in v0.8.2:**
- Five first-class adapters, all named after the recurring company/tool (john_stewart, gis_extraction, housekeys, cdn, alta)
- Housing Group elevated to first-class recurring administrator status via the cdn adapter (multiple cities reuse the same reliable workflow)
- Full freshness metadata (`last_seen` et al.) on every record from day one — foundation for delta runs and future trash compactor
- `extraction/` layer as the preferred structured path (new San José portal example)
- Registry nanny + `doctor --fix` guarantees every target has been sanitized against current TARGETS.md rules
- `no_public_list` fully operational: intentional skips produce human-visible documentation without polluting machine output
- cdn adapter robustly handles real-world protected document centers (PDF skipping, direct extraction, network interception, showdocument/docaccess resolution)
- Consistent logging style and quiet non-success paths (especially GIS)

**Current Reality:**
- The tool is now a repeatable, maintainable platform for any recurring administrator pattern
- Human-curated TARGETS.md + first-class adapters = low maintenance daily runs
- All records carry provenance and freshness for trustworthy downstream use
- Adding a new city that uses an existing administrator is now configuration, not new code

**Remaining Deliberate Scope Boundaries (Not Gaps)**
- Full "plug-and-play skill" packaging for Hermes / autonomous agents is intentionally deferred until the extraction spine and freshness model are more battle-tested in daily nonprofit use.
- High-frequency delta runs + trash compactor logic (using the existing `last_seen` / `first_seen` fields) are the next major feature layer.
- Replication guide for other counties/nonprofits exists in AGENTS.md + per-adapter PATTERN sections but will continue to be refined based on real usage feedback.
- Cities without any public structured source are correctly handled via `no_public_list` + human documentation rather than forced scraping.

---

## 3. The Contract (Agreed Scope)

### In Scope for v0.8 / v0.8.2 (Current Milestone — Delivered)
1. **First-class, maintainable adapters named after real recurring tools/companies**
   - Five production adapters: john_stewart, gis_extraction, housekeys, cdn, alta
   - Each with full Scope & Guardrails, Known Low-Value Patterns, and "PATTERN FOR NEW USE CASES"
   - Housing Group (multiple cities) handled via reusable cdn workflow
   - Alta Housing and HouseKeys treated as stable administrator patterns

2. **Freshness / delta foundation (first-class schema)**
   - Every record carries `last_seen`, `first_seen`, `source`, `source_url`, `expires_at`
   - Normalizer guarantees the shape; ready for trash-compactor / high-frequency delta runs

3. **Registry + operational guardrails**
   - `sanitize_target` nanny on every ingest
   - `doctor --fix` forces clean re-synchronization from TARGETS.md
   - `no_public_list` fully enforced with human-visible documentation (never pollutes CSV)

4. **High-quality extraction paths**
   - Preferred `extraction/` layer for structured portals (San José example)
   - Robust cdn handling for protected document viewers + PDF extraction
   - Consistent output shape across all paths

5. **Documentation & repeatability**
   - AGENTS.md + per-adapter docs describe exactly how to add new cities that use existing administrators
   - Logging is disciplined and production-appropriate (no spam on non-matching paths)

### Out of Scope (for now)
- Full county-wide coverage of every possible property (impossible via public scraping)
- Solving anti-bot protection on every city site
- Building a full web UI or notification system
- Multi-county support in v0.8 (design for it, implement later)
- Advanced LLM-based discovery (can be added later as optional enhancement)

### Success Criteria for v0.8 / v0.8.2 (Met)
- A nonprofit can run `python main.py --run` (after `doctor --fix`) against the current TARGETS.md and receive clean, deduplicated, freshness-annotated CSV + human-readable summaries.
- All three `no_public_list` cities are automatically skipped with clear human documentation and never appear in machine output.
- Adding support for a new city that uses Housing Group, Alta, HouseKeys, John Stewart, or municipal GIS is now a TARGETS.md row + (if truly new pattern) a small extension to an existing first-class adapter.
- The five first-class adapters + extraction layer cover the real recurring patterns in the county without city-specific one-off files.
- Logs are quiet and professional during normal daily runs; only meaningful activity is surfaced at INFO level.
- The freshness schema is present on every record, enabling future delta / trash-compactor work without schema migration pain.

### Discovery Philosophy (Original, 2026-05-18) + Current Adapter Standards (2026-05-21)

**Original Philosophy (still valid):**
Start from broad, Googleable county pages → conservative high-precision discovery → human curation into TARGETS.md → reliable extraction.

**Current Reality (v0.8.2):**
The project has matured into a platform of five first-class, company-named adapters plus an extraction layer. The emphasis is on **reliable, low-maintenance extraction** from known-good, human-curated targets. Adding a new city is usually just a row in TARGETS.md when it uses one of the established recurring administrators (Housing Group via cdn, Alta, HouseKeys, John Stewart, GIS).
- **Two distinct modes**:
  - `--discover` (or first run): Interactive bootstrap. Can be heavier. Uses search to propose targets. Human approves the initial list. During bootstrap, the system asks the user about auto-proposal preferences (review gate vs conservative auto-accept vs fully manual).
  - `--run`: Lightweight daily scrape of known targets only.
  - `--refresh-targets`: Heavyweight discovery pass that can propose additions/changes to the target list.
- **Human-curated by default in v0.8**. The system proposes; `TARGETS.md` remains the source of truth. Proposals can be gated or written to a review file.
- **Ongoing discovery** happens when `--refresh-targets` is used (or optionally triggered on signals during `--run` in later versions). It does **not** run on every lightweight `--run` by default.
- **Frequency expectation**: 1–2 times per day (e.g. 6am + 6pm). Heavy discovery can be configured to only run on one of the scheduled executions.
- **v0.85+**: Once the core spine is solid, we introduce more LLM-assisted discovery and feedback loops on top of the established structure. Not in v0.8 scope.

---

## 4. Maturity vs Contract (v0.8.2 Status)

| Area                        | Vision / Contract                                      | Current State (v0.8.2)                          | Status    | Priority |
|----------------------------|-------------------------------------------------------|--------------------------------------------------|-----------|----------|
| Target list independence   | Fully independent (`TARGETS.md`)                      | Excellent (20 rows, active/skipped split)       | Delivered | Done     |
| First-class adapters       | Named after tools/companies, reusable patterns        | Delivered (5 adapters: john_stewart, gis, housekeys, cdn, alta) | Delivered | Done     |
| Freshness / delta schema   | Every record carries last_seen / source / provenance  | Delivered across all paths + normalizer         | Delivered | Done     |
| Operational `no_public_list` | Intentional skips documented but never pollute output | Fully working (WARN + human sections in summaries) | Delivered | Done     |
| Registry & doctor          | Safe ingest, easy re-synchronization                  | `doctor --fix` + sanitize nanny                 | Delivered | Done     |
| Logging quality            | Professional, quiet, no spam on non-matching targets  | Good (GIS moved to debug, consistent prefixes)  | Delivered | Done     |
| Reusable as a Skill        | Easy for other nonprofits/agents                      | Strong (AGENTS.md + per-adapter PATTERN sections) | In Progress | Medium   |
| Clean daily output         | Actionable for data team                              | Very good (74 records, deduped, freshness)      | Delivered | Done     |
| Hermes / Agent ready       | Metadata + engine clearly separable                   | Good (registry + extraction + adapters)         | Delivered | Done     |

---

## 5. Recommended Next Steps (v0.8.2 Alignment Complete)

With the v0.8.2 spine solid (5 first-class adapters, freshness, registry, operational skips, clean logs), the natural next priorities are:

1. **Implement the trash compactor** (use `last_seen` / `first_seen` to produce "removed since last run" + expired records in daily output)
2. **High-frequency delta runs** (4×/day) now that freshness metadata is reliable
3. **Strengthen Housing Group / cdn patterns** if new document viewer quirks appear
4. **Ground-truth validation** on high-value targets (Gilroy, Palo Alto, San José, SCCHA)
5. **Polish README + replication guide** so another nonprofit can stand up their own instance quickly
6. **Optional `--review` gate** for `--refresh-targets` proposals

**Discovery Philosophy (locked for v0.8.x):** Human-curated TARGETS.md + `--discover` / `--refresh-targets` for proposals. No fully autonomous LLM discovery loops until the extraction + freshness + dedupe core is battle-tested in production use. v0.85+ territory.

---

## 6. Agreement (Updated for v0.8.2 — 2026-05-20)

This document now accurately describes the delivered v0.8.2 product.

**Key Decisions Locked (and verified in the clean run):**
- All targets go through the registry nanny (`sanitize_target`) on every load.
- `doctor --fix` is the single source of truth for re-synchronizing the DB from TARGETS.md.
- `no_public_list` targets produce clear WARN logs + human documentation in summaries but are never emitted into CSV or machine data.
- First-class adapters are the only way new recurring patterns are added (cdn for Housing Group document centers is the current shining example).
- Freshness metadata is non-negotiable on every record.
- Logging is production-appropriate: quiet by default, useful prefixes, debug for noisy internal attempts (GIS).
- Human stays in control via TARGETS.md curation.

**v0.8.2 Release Criteria (All Met):**
- [x] Five first-class adapters (john_stewart, gis_extraction, housekeys, cdn, alta) with excellent docs
- [x] Freshness schema live on every record
- [x] `no_public_list` fully operational and human-visible
- [x] Clean, quiet logs during a full `--run`
- [x] PROJECT_CONTRACT, AGENTS.md, and code in sync
- [x] Successful zero + clean re-run producing usable 74-record output

**Current Working Contract:** Section 7 (“Database Management Layer”) is now the active specification. The DB management tooling was implemented in v0.8.3 and all future changes must stay aligned with it.

**v0.8.3 Release:** Database Management Layer (Section 7) fully implemented + unit tested. Foundation for real validation work is now in place.

**Next phase:** City-by-city human verification using the new `db_manage.py` tooling, followed by trash compactor implementation.

---

## 7. Database Management Layer (Foundation for Validation & Trash Compactor)

### Purpose
As the project moves into disciplined city-by-city human validation, repeated pull/compare/reset cycles, and eventual delta/trash-compactor work, a lightweight, purpose-built database management capability is required.

This is **not** a general-purpose DBA tool. It is a bespoke “DBA in a box” that makes the most common operational and validation pain points safe and scriptable.

### Core Principles
- Clear separation between **safe diagnostics** and **intentional management**.
- Human remains in control; destructive actions require explicit confirmation.
- Conservative defaults with easy-to-reach knobs.
- All operations are idempotent where possible.
- Documentation and tooling must be usable by both the primary developer and future data analysts/nonprofit staff.

### Responsibility Split

**`scripts/db_manage.py`** (Management Tool)
- All structural and data-changing operations: schema initialization, database drop/reset, pruning, snapshots, restores, and run history inspection.
- This is the “sharp tools” script. Humans and scripts call it when they *intend* to change state.

**`scripts/doctor.py`** (Diagnostic & Safe Recovery Tool)
- First-line tool for health checks and basic recovery.
- May automatically perform **super-safe, non-destructive** actions (e.g., SQLite checkpoint replay after a hard stop, hot backup of the live `.db` file, basic integrity verification).
- Must **never** perform schema repairs, migrations, or data pruning.
- When structural or management action is required, Doctor must clearly state the problem and direct the user to `db_manage.py` plus the relevant README section.

### Configuration
Human-tunable policy lives in:
```
~/.housing-list-search/settings.yaml
```

Example (initial):

```yaml
database:
  prune:
    default_not_seen_days: 45
```

The default is intentionally conservative. It can be lowered over time as validation confidence increases. CLI flags always override the configured default.

### Prune Policy (Priority Order)
Pruning follows this strict order and is always driven by the existing freshness fields (`last_seen`, `first_seen`, `expires_at`):

1. Any record whose `expires_at` date is in the past.
2. Any record whose `last_seen` timestamp is older than the configured “not seen” threshold (default 45 days).
3. `--all-stale` executes the above rules in a single, idempotent pass.

Preferred CLI style for the threshold is `--not-seen-since` (days). There is no automatic age-based pruning outside of the configured window.

### Safety Model
Destructive commands require an explicit confirmation flag using the pattern:

- `--confirm DROP`
- `--confirm RESET`

Once the exact confirmation string is supplied on the command line, the action proceeds without further interactive prompts. This supports both interactive safety and scripted workflows.

### Snapshot & Restore Format
Snapshots are designed for validation workflows and reproducibility:

- `python scripts/db_manage.py snapshot --name <label>`
- Produces a timestamped `.tgz` containing:
  - `current_full.csv`
  - `manifest.json` (timestamp, git commit, filters applied, per-authority counts, active prune policy, etc.)
- Restore replays the captured `current_full.csv` into the live database (with appropriate safeguards).

### Run History
A lightweight `run_history` table records every significant `db_manage.py` operation for auditability during heavy validation periods.

### Database Schema (Core Tables for Management Layer)

```
┌──────────────────────────┐
│        housing_records   │
├──────────────────────────┤
│ id (PK)                  │
│ authority                │
│ property_name            │
│ ... (existing fields)    │
│ last_seen                │
│ first_seen               │
│ source                   │
│ source_url               │
│ expires_at               │
└──────────────────────────┘

┌──────────────────────────┐     ┌──────────────────────────┐
│       run_history        │     │        snapshots         │
├──────────────────────────┤     ├──────────────────────────┤
│ id (PK)                  │     │ id (PK)                  │
│ timestamp                │     │ name                     │
│ command                  │     │ created_at               │
│ authority_filter         │     │ manifest (json)          │
│ rows_before              │     │ file_path (.tgz)         │
│ rows_after               │     └──────────────────────────┘
│ notes                    │
└──────────────────────────┘
```

(The full `housing_records` and `targets` tables already exist; the management layer adds `run_history` and uses the existing freshness columns.)

### Doctor Behavior (Explicit Boundaries)
Doctor is allowed to:
- Run SQLite `PRAGMA integrity_check` / `quick_check`
- Trigger safe WAL checkpoint replay
- Create a hot backup copy of the live database file
- Report freshness health and obvious data issues

Doctor must **never**:
- Run schema-altering SQL (`ALTER`, `DROP TABLE`, migrations, etc.)
- Execute any prune logic
- Silently repair or compact data without explicit user intent via `db_manage.py`

When Doctor detects a condition that requires `db_manage.py`, it prints a clear message directing the user to the management script and the Database Management section of the README.

### Non-Goals (for this increment)
- Full general-purpose database administration UI or web interface
- Automatic background pruning without human initiation
- Cross-database (Postgres, etc.) support
- Complex point-in-time recovery beyond simple named snapshots

---

## 6. Agreement (Updated for v0.8.2 — 2026-05-20)

*This contract is a living record of shared understanding. It will be updated when the product evolves.*
