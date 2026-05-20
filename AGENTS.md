# AGENTS.md - Project Notes for Grok / Hermes

## Permission Mode (Important for this project)

The user (Joshua) strongly dislikes constant permission approval prompts when actively building and testing code, especially Python scripts and terminal commands.

**Command to reduce friction:**
```
/always-approve on
```

This enables always-approve mode and skips most tool permission checks for the session.

Turn it off with:
```
/always-approve off
```

Use this when the user says things like "fewer questions", "just do the work", "stop asking every time", etc.

This was documented after repeated frustration during the housing-list-search development session in May 2026.

## Current Project Focus (v0.8.2 — as of May 2026)

- Five first-class adapters, all named after the recurring tool/company (never the city):
  - `john_stewart.py` — vendor + custom front-end properties (SCCHA and others)
  - `gis_extraction.py` — municipal GIS layers + federated managers (Cupertino + Rise Housing reference)
  - `housekeys.py` — delegated registration/notification/lottery portals
  - `cdn.py` — CDN/WAF-protected document viewers (DocumentCenter, showdocument, docaccess) — primary path for Housing Group cities
  - `alta.py` — Alta Housing delegated administrator pattern (Palo Alto reference)
- Preferred high-quality `extraction/` layer for structured portals (San José example).
- Full freshness schema (`last_seen`, `first_seen`, `source`, `source_url`, `expires_at`) on every record.
- Registry nanny layer + `scripts/doctor.py --fix` for safe, repeatable TARGETS.md ingestion.
- Operational `no_public_list` handling with human-visible documentation.
- Strong emphasis on clean, quiet logging and "PATTERN FOR NEW USE CASES" documentation in every adapter.

Last updated: 2026-05-22 (post Gilroy cdn DocumentCenter availability list improvements — v0.8.4)

## Adapter Development Standards

The objective is to produce consistent, maintainable adapters that can be extended and improved over time as more municipalities and data patterns are encountered.

### Naming
- Name the adapter after the **tool, vendor platform, or data source**, not the city or housing authority.
  - Preferred examples: `john_stewart.py`, `gis_extraction.py`, `rentcafe_bmr.py`
  - Avoid city-specific names such as `cupertino.py` or `sccha.py`.

### Scope & Boundaries
- Extract only data that is **publicly published** on the pages being scraped.
- Do not pursue unlisted staff contacts, internal directories, or information that is not openly available.
- For municipal “city as coordinator” models:
  - Assume the city or its designated administrator manages the official waitlist.
  - Locating individual public servants is out of scope.
- Anonymous applicant lottery waitlists (lottery numbers, preference points, and position only) are generally low value and should be treated as such.

### Data Assumptions
- When a municipality publishes its affordable housing portfolio through a GIS layer, treat the GIS data as the authoritative source for property names and unit counts until operational experience indicates otherwise.

### Known Low-Value Patterns
- Anonymous lottery-style waitlist PDFs that contain only applicant identifiers and rankings (no property-level information).
- Overly broad keyword scanning on sites where more structured data is available.
- These patterns should be noted in the adapter but generally deprioritized for structured extraction.

### Structure & Documentation Requirements
- Keep all logic for a single tool or platform in one file.
- Every adapter must include a clear module docstring containing:
  - A description of the pattern it addresses.
  - Current design assumptions.
  - An explicit **Scope & Guardrails** section.
  - Guidance for future extension of the pattern.
- Use consistent public entry points and helper organization so that new adapters follow a predictable structure.

### Extension Philosophy
- When a new city presents a similar data model, extend the existing adapter or create a focused variant inside the same file.
- When a meaningfully different pattern is discovered, create a new adapter and document the pattern here.
- The intent is for the collection of adapters to become more capable and consistent over time through incremental, well-documented additions.

### Target Ingestion Safety ("Nanny Layer")
`registry.py` contains a `sanitize_target()` function that is run on every row during `load_targets_to_db()` (first acquisition from TARGETS.md).

It performs:
- Scheme validation on URLs (only `http://` and `https://` allowed)
- Control character stripping
- Length limits on all text fields
- Normalization of the `scraping_measures` column
- Basic detection of prompt-injection-style language in notes (for future LLM/agent contexts)

Bad rows are logged as warnings and skipped. The DB always contains sanitized data.

This is our lightweight defense against malformed, accidentally broken, or malicious entries in TARGETS.md without turning the file into a fortress. The human still owns the list; the code just refuses to blindly trust it.

These standards support reliable, maintainable work as the project encounters a wider variety of municipal data sources.