# Agent memory — housing-list-search (repo-local)

Persistent notes for AI contributors. **Not** a global skill — lives only in this repo.

---

## Architecture review (2026-07-09) — deepen candidates

Report: `$TMPDIR/architecture-review-20260709-162428.html` (temp; not in repo).

| Vikunja | Topic | Priority |
|---------|-------|----------|
| **#1059** | Deepen Disappearance (#1+#3) — machine Diff labels + staff projection | **done** (pure classify; freshness shim) |
| **#1060** | Make Access deep (#2) | **done** (adapters import access only) |
| **#1061** | RunReview / Needs Review spine (#4) | **done** (assess→build→surface) |
| **#1062** | Finish or delete CanonicalListing (#5) | **done** (deleted half-depth type) |
| **#1063** | Staff Publish (#6) | **done** (staff_publish.publish_staff_run) |
| #1064 | Bloom path adapters (#7, speculative) | backlog |

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

## Open Vikunja — Housing Search (#9)

**Paperwork 2026-07-09:** closed **47** zombie/done tickets (ponytail #969–#981, deep-critique #983–#992 cluster, ops ADR #708–#739/#849, listing/dispatch overlaps #660/#785/#788/#826–#830, etc.). Notes prefixed `DONE (paperwork 2026-07-09)`.

### High/med concrete (2026-07-09) — shipped

`df7669d` Playwright pool (#761 #769 #987) + low-yield (#789); `bf399c3` export confirmed_this_run (#659), property-only open list (#989), PDF magic (#791), prune docs (#657), OCR lock (#768), token logs (#790), RUN_EVENT (#988), docaccess/policy (#658), dead-weight N/A (#792).

**Deferred by design:** #770 portal smoke remains weekly (no daily hammer).

### Deepen-seam hygiene (2026-07-09) — shipped

`…` measure registry drift API + doctor check (#828 #799); RunPipeline collect/persist/publish (#782); coerce_adapter_records at dispatch (#801). Closed outdated arch duplicates #781 #794 #802 #798 as paperwork.

### Still open (investigate + architecture)

| Priority | Theme | IDs |
|----------|--------|-----|
| investigate | dedupe STALE, integration breadth, XHR policy, pagination alert, throttle races, marker license | **#661 #662 #773 #775 #776 #778 #793** |
| architecture (optional) | seams, RunPipeline phases, measure registry, N+1 upsert, adapters | **#781–#784 #786 #794–#802 #828 #797 #795 #796 #798 #799 #801** |

**Domain bar:** down ≠ gone.

