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

Epic **#389** (portable routing + record identity) — **#417**, **#419** done 2026-07-01; still open: **#432**.

| Commit | Work | Vikunja |
|--------|------|---------|
| `7346c40` | `persistence_url()` surrogate keys; `JOHN_STEWART_AUTHORITY` | #417, #419 |

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

## Vikunja housekeeping (2026-07-01)

**Deleted duplicates:** #424 (dup #394), #425 (dup #401), #426 (dup #402), #427 (dup #405), #428 (dup #409), #429 (dup #422).

**#410** marked **[SUPERSEDED]** — pdfplumber-only migration cancelled; unified `extraction/pdf.py` + optional marker (#624, #625).

---

## Dev shortcuts

- Unit tests: `HLS_DISABLE_MARKER_PDF=1 .venv/bin/python -m pytest tests/ -m "not integration"`
- Single-target smoke: `python main.py --run --target "Gilroy"`
- Re-ingest targets: `python scripts/doctor.py --fix`

---

## Session preferences

Joshua prefers fewer permission prompts during active dev (`/always-approve` in Claude Code). See `AGENTS.md` session friction note.