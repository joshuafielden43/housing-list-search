# Housing List Aggregator — Project Contract (v0.1)

**Note:** This document originated as v0.1 but has been updated to reflect the actual state at v0.8.1. The filename is kept for historical continuity.

**Project Name:** Housing List Aggregator (Santa Clara County + Portable Skill)  
**Status:** Alpha (First-class Adapters + Guardrails Established)  
**Date:** 2026-05-18  
**Last Updated:** 2026-05-21 (v0.8.1 + post-release QA: operational no_public_list skipping + WARN logging, HouseKeys first-class adapter, registry helpers, 403/404 handling, doctor coverage)  
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

As of v0.8.1 we have:

**Core Components:**
- `TARGETS.md` as the human-editable source of truth for targets
- SQLite registry for targets
- Dedicated high-quality adapters:
  - `john_stewart.py` (first-class, consolidated for direct + custom front-ends)
  - `gis_extraction.py` (first-class reference for municipal GIS + federated manager patterns)
  - `housekeys.py` (first-class reference for delegated registration/notification portals)
- Operational `no_public_list` enforcement in registry + CLI (with WARN logging to human outputs only)
- Extraction layer (`extraction/`) with PDF table extraction and San José portal support
- `current_full.csv` + `daily_summary.md` outputs
- Deduplication across sources
- Basic daily run path in CLI
- Explicit Scope & Guardrails + Known Low-Value Patterns documented in adapters and AGENTS.md

**Key Achievements in v0.8.1:**
- Two reference first-class adapters with clear Scope & Guardrails sections
- Standardized "name after the tool, not the city" rule
- Explicit handling of "city as coordinator / federated managers" model (e.g. Cupertino GIS + Rise Housing + multiple nonprofits)
- Documented low-value patterns (anonymous lottery waitlists, broad keyword scraping)
- Clear extension philosophy so future one-offs can be added without archaeology

**Current Reality:**
- Strong, maintainable adapter pattern established
- Discovery is de-emphasized in favor of reliable extraction from known good sources
- Focus on actionable, deduplicated records with real contacts and application paths where available

**Gaps vs Original Vision:**
- Still not fully packaged as a "plug-and-play skill" for Hermes/other agents
- Daily runner exists but could be more robust
- Documentation for replication by other nonprofits is improving but not complete
- Some cities remain low-signal (require per-city one-off work)

---

## 3. The Contract (Agreed Scope)

### In Scope for v0.8 / v0.8.1 (Current Milestone — Largely Delivered)
1. **First-class, maintainable adapters**
   - Consolidated, well-documented adapters named after tools (John Stewart, GIS Extraction)
   - Explicit Scope & Guardrails in every adapter
   - Known Low-Value Patterns documented
   - Clear rules on what is in-scope vs out-of-scope (no hunting individuals, only published data, etc.)

2. **Support for real municipal patterns**
   - Centralized vendor platforms (John Stewart)
   - City-coordinated / federated models (GIS portfolio + multiple managers, e.g. Cupertino)
   - Ability to extend cleanly for new one-off scenarios

3. **Operational foundation**
   - Deduplication across sources
   - Stable CSV + summary outputs
   - Basic daily run capability

4. **Documentation & Portability**
   - Strong agent instructions (AGENTS.md) with extension guidance
   - Clear philosophy so the skill improves over time without losing consistency

### Out of Scope (for now)
- Full county-wide coverage of every possible property (impossible via public scraping)
- Solving anti-bot protection on every city site
- Building a full web UI or notification system
- Multi-county support in v0.8 (design for it, implement later)
- Advanced LLM-based discovery (can be added later as optional enhancement)

### Success Criteria for v0.8 / v0.8.1 (Updated)
- A nonprofit can run the tool against real county data and get usable, deduplicated records.
- New one-off municipal sources can be added by extending existing first-class adapters without creating technical debt or requiring deep archaeology.
- The project has clear, written guardrails (Scope, Known Low-Value Patterns, "only published data", city manages the list, etc.) so future work stays consistent.
- Another nonprofit or agent can understand the architecture and extension model from the code + AGENTS.md.

### Discovery Philosophy (Original, 2026-05-18) + Current Adapter Standards (2026-05-21)

**Original Philosophy (still valid):**
Start from broad, Googleable county pages → conservative high-precision discovery → human curation into TARGETS.md → reliable extraction.

**Current Reality (v0.8.1):**
The project has matured into a set of first-class adapters with explicit guardrails. The emphasis has shifted from pure discovery to **reliable, extensible extraction** with clear rules so that one-off scenarios (especially GIS + federated managers) can be handled consistently and the overall skill improves over time.
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
