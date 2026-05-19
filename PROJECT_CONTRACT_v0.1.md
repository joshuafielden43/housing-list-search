# Housing List Aggregator — Project Contract (v0.1)

**Project Name:** Housing List Aggregator (Santa Clara County + Portable Skill)  
**Status:** Alpha (Aligned on Discovery Philosophy)  
**Date:** 2026-05-18  
**Last Updated:** 2026-05-18 (Discovery modes + versioning clarified)  
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

As of the latest run we have:

**Core Components:**
- Interactive first-run discovery (`--discover`) that builds `TARGETS.md`
- SQLite registry for targets
- Polite static scraper + generic keyword-based extractor
- Playwright support for JS-heavy sites (San José portal, SCCHA)
- Dedicated SCCHA adapter (good quality)
- John Stewart Company support
- `current_full.csv` output (ready for database import)
- `daily_summary.md` for internal tech mailing list
- Basic changelog diffing

**Current Metrics (latest run):**
- 17 targets discovered
- ~149 listings extracted per run
- Playwright successfully pulling from SCCHA (22) and San José (9–12)
- Many city sites still return 403 or very noisy data

**Strengths:**
- Auto-discovery works and is human-editable
- Modular adapter pattern started
- Playwright integration exists for dynamic sites
- Outputs are useful for a data team

**Gaps vs Vision:**
- Summary is still noisy (duplicates, long junk titles, closed waitlists appearing)
- Not yet packaged as a clean "skill" with clear metadata for agents
- No review/approval gate yet
- No easy daily runner + cron instructions
- Not yet documented for easy replication by another nonprofit
- Some major city sites (Sunnyvale, Mountain View, etc.) are blocked or return poor data
- No clear versioning or release process

---

## 3. The Contract (Agreed Scope)

### In Scope for v0.8 (Next Milestone)
1. **Clean, usable daily output** for a nonprofit data/tech team
   - Significantly improved title cleaning + deduplication in parsers
   - Better filtering of closed/old listings from the "Open" section
   - Clear note about blocked sites

2. **Make it a proper reusable Skill**
   - Clear `TARGETS.md` + metadata that an agent can read
   - Simple `run_daily.sh` + instructions
   - Minimal README that explains how another nonprofit can fork/adapt it

3. **Hermes-friendly + Portable**
   - Document the engine + metadata structure so it can be understood by other agents
   - Keep dependencies reasonable (Playwright is acceptable but noted)

4. **Basic operational quality**
   - Optional `--review` gate before final files are written
   - Stable outputs (`current_full.csv` + clean `daily_summary.md`)

### Out of Scope (for now)
- Full county-wide coverage of every possible property (impossible via public scraping)
- Solving anti-bot protection on every city site
- Building a full web UI or notification system
- Multi-county support in v0.8 (design for it, implement later)
- Advanced LLM-based discovery (can be added later as optional enhancement)

### Success Criteria for v0.8
- A nonprofit staff member can run the tool daily and get a **clean, actionable** list of currently open waitlists/lotteries.
- Another nonprofit in a different county can read the code + `TARGETS.md` and understand how to adapt it in < 2 hours.
- The tool produces stable, importable CSV + human-readable summary.

### Discovery Philosophy (Agreed 2026-05-18)
- **Two distinct modes**:
  - `--discover` (or first run): Interactive bootstrap. Can be heavier. Uses search to propose targets. Human approves the initial list. During bootstrap, the system asks the user about auto-proposal preferences (review gate vs conservative auto-accept vs fully manual).
  - `--run`: Lightweight daily scrape of known targets only.
  - `--refresh-targets`: Heavyweight discovery pass that can propose additions/changes to the target list.
- **Human-curated by default in v0.8**. The system proposes; `TARGETS.md` remains the source of truth. Proposals can be gated or written to a review file.
- **Ongoing discovery** happens when `--refresh-targets` is used (or optionally triggered on signals during `--run` in later versions). It does **not** run on every lightweight `--run` by default.
- **Frequency expectation**: 1–2 times per day (e.g. 6am + 6pm). Heavy discovery can be configured to only run on one of the scheduled executions.
- **v0.85+**: Once the core spine is solid, we introduce more LLM-assisted discovery and feedback loops on top of the established structure. Not in v0.8 scope.

---

## 4. Current State vs Contract Gap Analysis

| Area                        | Vision / Contract                  | Current State          | Gap Level | Priority |
|----------------------------|------------------------------------|------------------------|-----------|----------|
| Target list independence   | Fully independent (`TARGETS.md`)   | Good                   | Low       | Done     |
| Reusable as a Skill        | Easy for other nonprofits/agents   | Partial                | Medium    | High     |
| Clean daily output         | Actionable for data team           | Noisy                  | High      | High     |
| Hermes / Agent ready       | Metadata + engine clearly separable| Partial                | Medium    | Medium   |
| Operational quality        | Review gate, daily runner          | Missing                | Medium    | Medium   |
| Portability documentation  | Clear instructions for replication | Missing                | High      | High     |
| Parser quality             | Good titles, low noise             | Improving but noisy    | Medium    | High     |

---

## 5. Recommended Next Steps (After Alignment)

Once we agree on this contract, the immediate priorities are:

1. **Finish parser improvements** (cleaner titles + better dedup) — highest usability win right now
2. **Create `run_daily.sh`** + simple usage instructions
3. **Write a clean, short README** focused on "How another nonprofit can use/adapt this"
4. **Add optional `--review` gate** (supports human-curated workflow)
5. **Ground truth validation** on a few high-value targets (planned)
6. **Tag and document v0.8** as the first usable, human-curated nonprofit skill release

**Note on Discovery**: We are deliberately keeping discovery human-curated and command-driven (`--discover` + `--refresh-targets`) in v0.8. More autonomous/LLM-driven loops are planned for v0.85+ once the core spine is solid.

---

## 6. Agreement (Updated 2026-05-18)

This document represents the **current shared understanding** of what we are building.

**Key Decisions Locked:**
- Bootstrap (`--discover`): Interactive, search-assisted, human approves initial targets + sets auto-proposal preference.
- Runtime commands: `--run` (lightweight) and `--refresh-targets` (heavy discovery) are deliberately separate.
- v0.8 = Human-curated workflow. System proposes; human stays in control.
- v0.85 = Introduce LLM-assisted discovery loops once the core spine is solid.
- Discovery is **not** fully autonomous in v0.8. It is command-driven and human-reviewed.

- [x] Original vision is correctly captured
- [x] Current built state is accurately described
- [x] Contract / scope for v0.8 (including discovery philosophy) is agreed
- [ ] We will pause large new features until parser quality + basic operational UX are solid

**Next action:** User will pick this up in the morning. Immediate focus areas: parser cleanup, daily runner script, and review gate.

---

*This contract can be updated as we learn. The goal is shared clarity, not rigidity.*
