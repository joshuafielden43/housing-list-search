# Agent memory — housing-list-search (repo-local)

Persistent notes for AI contributors. **Not** a global skill — lives only in this repo.

---

## Tracker (canonical)

| Field | Value |
|-------|-------|
| **System** | [Vikunja](https://vikunja) — Joshua's task tracker |
| **Project ID** | **9** |
| **Project title** | Housing Search |
| **Description** | Affordable housing waitlist aggregator, Santa Clara County — this repo |

**Rule:** File new work in Vikunja project **#9**. Do **not** open GitHub issues for internal architecture/triage unless Joshua explicitly asks.

GitHub issues **#8–#14** (architecture review, Jul 2026) were filed by mistake during an audit session. All closed. Treat as historical crosswalk only — see below.

---

## Architecture sprint (2026-07-01) — shipped on `main`

| Commit | Work | Vikunja |
|--------|------|---------|
| `119525b` | Listing seam (`listing.py`), PDF merge, `marker_pdf.py`, `CONTEXT.md` | #415, #420, #624, #625 |
| `cfcee2f` | `dispatch.py`, bloom measure gating, thin `runner.py` | #421, #422 |
| `4308319` | `pipeline.py`, thin `cli.py` | #623 |
| `9b066b5` | `freshness.py`, changelog ↔ diff alignment | #415, #626 |
| `a26efa1` | `AGENTS.md` v0.8.7 | #409 |

Epic **#389** (portable routing + record identity) — **complete** as of 2026-07-01 (#432 schema consolidation).

| Commit | Work | Vikunja |
|--------|------|---------|
| `7346c40` | `persistence_url()` surrogate keys; `JOHN_STEWART_AUTHORITY` | #417, #419 |
| `29874c3` | `schema.py` — unified DDL; `DEFAULT_DB_PATH` | #432 |

---

## Backlog sprint (2026-07-04) — shipped on `main`

| Commit | Work | Vikunja |
|--------|------|---------|
| `0e154df` | ADRs 0001–0004 + CONTEXT operational-review vocabulary | — |
| `7af6c3b` | Suspicious Zero detection + Needs Review in `daily_summary.md` | ADR-0002/0004 |
| `19184f3` | Validated Zero metadata in TARGETS.md | ADR-0003 |
| `2d11dc7` | Changelog SCRAPE_FAILED alignment; civicplus failure propagation | #653, #654 |
| `dcfcabc` | Staff outputs, ops hardening, URL policy, CI ruff/pip-audit | #655, #656, #711–#730 |
| `785df5e` | measure_registry, TARGETS shape check, ground_truth vendor bounds | #716, #724 (partial) |
| `71247c6` | needs_review webhook, measure registry unify, #725 tests | #713, #740, #725, #741 |

| `320c453` | #413 pdfplumber default (ADR-0005), #416 lockfiles, #723 Playwright throttle | #413, #416, #723 |

---

## GitHub ↔ Vikunja crosswalk (audit trail only)

| GitHub issue | Vikunja task(s) | Topic |
|--------------|-----------------|-------|
| #8 | #421, #422 | dispatch unification |
| #9 | #415, #420 | listing seam |
| #10 | #624 | PDF stack merge |
| #11 | #623 | RunPipeline |
| #12 | #415, #626 | freshness reconciliation |
| #13 | #422 | measure registry (dup of #8) |
| #14 | #625 | marker-pdf fallback |

---

## Vikunja housekeeping

**Deleted duplicates (2026-07-01):** #424–#429.

**#410** — historical pdfplumber-only spike; superseded by unified `extraction/pdf.py` + marker (#624, #625). **Implemented 2026-07-04:** pdfplumber-only default per ADR-0005 (#413).

---

## Coverage baseline (2026-07-01 full `--run`)

| Metric | Value |
|--------|-------|
| Deduped listings this run | 437 |
| `current_full.csv` rows | 435 (post-prune) |
| Targets in TARGETS.md | 24 (20 active, 4 `no_public_list`) |

**Pruned 2026-07-01:** migration STALE via `python scripts/db_manage.py prune --from-diff`. Do **not** use `--not-seen-since 45` for migration churn; do **not** use `--all-stale`.

---

## Dev shortcuts

- Hooks: `npm install` after clone (Husky; Vikunja **#650**)
- **Prod deps:** `uv pip install -r requirements.txt` (or `requirements.lock` for pinned installs)
- **Dev/CI deps:** `uv pip install -r requirements-dev.txt` (pytest, ruff)
- **OCR tier:** `uv pip install -r requirements-ocr.txt` (marker-pdf, GPL — hard PDFs only)
- Full local gate: `npm run check` (ruff + `doctor --dry-run` + unit tests)
- Unit tests: `HLS_DISABLE_MARKER_PDF=1 .venv/bin/python -m pytest tests/ -m "not integration"`
- Parallel targets: `HLS_MAX_TARGET_WORKERS=3` (default); per-host robots cache + throttle in `robots_cache.py` / `host_throttle.py`; Playwright uses `playwright_nav.safe_goto()` throttle
- Single-target smoke: `python main.py --run --target "Gilroy"`
- Re-ingest targets: `python scripts/doctor.py --fix`
- **Secrets:** `~/.hermes/.env` (canonical for cron/Hermes). Repo `.env` optional for local runs; `run_daily.sh` sources repo `.env` if present. Vars: `HLS_VIKUNJA_URL`, `HLS_VIKUNJA_TOKEN`, `HLS_NEEDS_REVIEW_WEBHOOK`

---

## Session preferences

Joshua prefers fewer permission prompts during active dev (`/always-approve` in Claude Code). See `AGENTS.md` session friction note.

### How to respond to Joshua's ideas (2026-07-04)

**Contract:** Joshua brings ideas; the agent stress-tests them. Say yes, no, or "yes but only if X" — **without making Joshua defend having the idea**, and **without citing "the current thing already works" as a veto** on forward-looking design or license decisions.

**Failure modes to avoid:**

| Don't | Do instead |
|-------|------------|
| Offer a replacement, then argue the incumbent wins because today's smoke test passes | Name the **decision frame** (license fix vs architecture vs ops) and answer **only** that frame |
| Present multiple tracks and argue against whichever is convenient | Pick a default recommendation; list tradeoffs once |
| Conflate "Gilroy integration test green" with "don't change the PDF stack" | Separate **observed state** from **what we ship tomorrow** |

When Joshua asks "replace X?" or "why not Y?", answer the replacement question directly. Incumbent success is relevant only if they asked "should we change anything at all?"

---

## PDF stack (ADR-0005, implemented 2026-07-04, Vikunja #413)

| Tier | Package | Role |
|------|---------|------|
| **Default** (`requirements.txt`) | **pdfplumber only** | Tables, text lines, flyer heuristics |
| **Hard PDFs** (`requirements-ocr.txt`) | **marker-pdf** (last resort) | Scanned/scrambled PDFs; GPL; OCR host — not daily-cron default |

Pipeline: pdfplumber tables → flyer heuristics → line-regex → marker fallback.

**Do not** put marker or pymupdf in default `requirements.txt` for license reasons.

**Do not** use crawl4ai / searxng for core `--run` — curated `TARGETS.md` + platform adapters. Fine for out-of-band human research when adding cities.

---

## Deployment posture (2026-07-04)

**Local-first, not a public release.** Primary operator is Joshua on Hermes/cron. If shared at all, audience is **one person** — not a forkable OSS onboarding story. Revisit onboarding fixtures (#412) only if release posture changes or something novel warrants wider distribution.

Implications:
- Skip committed baseline snapshots, release assets, and fork-oriented README paths unless scope changes.
- Existing local tooling is enough: `db_manage.py snapshot`, runtime `current_full.csv` / `housing_registry.db`, `doctor --fix`.
- Docs should describe operator workflow, not "clone and import baseline."

---

## Vikunja reverification sync (#720, #737 — implemented)

When `HLS_VIKUNJA_URL` + `HLS_VIKUNJA_TOKEN` are set, `notify_needs_review()` upserts open `[Reverify] {authority}` tasks in project **#9** (default). One task per authority; updates description on repeat signals. Does not auto-close — operator closes after TARGETS.md fix.

---

## Open Vikunja — needs Joshua

| Task | Why |
|------|-----|
| **#982** [EPIC] Ponytail cleanup batch (13 items) | delete/YAGNI/shrink legacy: normalizer.py, generic_scraper, playwright_scraper fallbacks, run_target wrapper, freshness helpers, doctor prune-snapshots, lint-staged, artifacts wrapper, city shims, records_to_markdown, GIS _normalize, plus 2 shrinks. Net -550..-850 LOC. See subtasks #969–#981. |

**Deferred (local-first):** **#412** onboarding baseline; **#423** PR live integration tests.

**Exploratory (optional):** **#407** Playwright host allowlist — scope memo only unless Joshua wants tightening.

**Closed epics (2026-07-04 doc groom):** **#388** docs/onboarding (#412 deferred, README operator-focused); **#390** operability (run_daily, throttle, needs_review webhook, CI, integration-weekly).