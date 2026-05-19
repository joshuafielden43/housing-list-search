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

## Current Project Focus (as of last update)

- Building first-class, well-documented adapters for different housing data sources.
- Reference adapter: John Stewart platform (consolidated handling of direct + custom front-ends).
- New pattern: GIS Extraction for municipal "city as coordinator" models (e.g. Cupertino BMR with GIS portfolio + federated managers).
- Establishing clear Scope & Guardrails, Known Low-Value Patterns, and extension guidance so adapters can be reliably extended over time.
- Emphasis on consistency, maintainability, and avoiding "archaeology" when extending for new one-off scenarios.

Last updated: 2026-05-21

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

These standards support reliable, maintainable work as the project encounters a wider variety of municipal data sources.