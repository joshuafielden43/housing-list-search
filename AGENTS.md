# AGENTS.md — Notes for AI Contributors and Future Maintainers

This file is the handoff document for AI systems (and humans) picking up this project. It explains the architecture, the naming conventions, the active adapters, and the patterns that have been established so you don't re-derive them from scratch.

Read this before writing any code. Read `SOUL.md` for the mission guardrails.

---

## Session friction note

Joshua dislikes constant permission approval prompts during active development.

**In Claude Code:** `/always-approve` enables auto-approval for the session. Use when Joshua says things like "fewer questions", "just do the work", "stop asking every time".

---

## Current state (v0.8.5, 2026-06-05)

Six first-class adapters, all named after the recurring **platform or vendor** (never the city):

| Adapter | Pattern | Reference city |
|---|---|---|
| `extraction/bloom_housing.py` | Bloom Housing Next.js — SSR + REST API + Playwright fallback | San José (SSR), MTC Doorway (API) |
| `adapters/john_stewart.py` | Vendor portal, custom front-end | SCCHA properties |
| `adapters/gis_extraction.py` | Municipal GIS layers + federated managers | Cupertino + Rise Housing |
| `adapters/housekeys.py` | Registration/notification/lottery portal | Morgan Hill, Gilroy, Los Gatos, Mountain View, Milpitas |
| `adapters/cdn.py` | CDN/WAF-protected document viewers | Housing Group cities (Campbell, Los Altos, Menlo Park, Half Moon Bay), Gilroy PDFs |
| `adapters/alta.py` | Alta Housing delegated administrator | Palo Alto |

High-quality structured extraction lives in `extraction/`. Adapters in `adapters/` handle messier or registration-only cases. The dispatcher in `cli.py` routes targets to the right adapter based on `scraping_measures` and URL patterns.

---

## Adapter naming rule

**Name adapters after the tool, vendor platform, or data source — never the city.**

- `bloom_housing.py` — not `san_jose.py`
- `housekeys.py` — not `morgan_hill.py`
- `gis_extraction.py` — not `cupertino.py`
- `john_stewart.py` — not `sccha.py`

This rule exists because the same platform is used by multiple cities. A city-named adapter file is always wrong — it can't scale to the next city that uses the same backend, and it trains future contributors to make the same mistake.

---

## Bloom Housing platform (most complex adapter)

`extraction/bloom_housing.py` handles any deployment of the open-source Bloom Housing platform (github.com/bloom-housing/bloom). Three extraction paths tried in order:

### Path 1 — SSR via `__NEXT_DATA__` (preferred)

Some Bloom instances use `getServerSideProps` on `/listings`. The full listing payload is embedded in a `<script id="__NEXT_DATA__">` tag before the browser receives the page.

**Critical**: the data path is `data["props"]["pageProps"]`, NOT `data["pageProps"]`. The top-level `pageProps` key does not exist in Next.js. Accessing it directly silently returns `{}` and yields zero listings. This was a confirmed bug in the original San José extractor.

Known SSR instances: `housing.sanjoseca.gov`

### Path 2 — REST API (CSR instances)

Some Bloom instances use client-side rendering. The browser fetches listings via XHR POST to `/api/adapter/listings/combined`. Required headers (discovered by intercepting Playwright network traffic):
- `jurisdictionname: {name}` — from the Bloom admin config
- `appurl: https://{host}`
- `language: en`

Returns `{"items": [...], "meta": {...}}`. Apply `city_filter` client-side to isolate a specific city from a county-wide feed.

Known API instances: `housingbayarea.mtc.ca.gov` (MTC Doorway Bay Area)

To add a new API instance: add it to `_API_INSTANCES` in `bloom_housing.py`.

### Path 3 — Playwright fallback

Only activates if both SSR and API yield zero results. Launches headless Chromium and intercepts JSON network responses. Slow (~10s) — if it's activating regularly, investigate whether the site architecture changed.

Signs the fallback is running: log line `[Bloom] Playwright fallback activated for {url}`.

### San José and MTC Doorway share the same backend

The same listing UUIDs appear on both `housing.sanjoseca.gov` and `housingbayarea.mtc.ca.gov`. San José is one jurisdiction in the MTC Doorway county-wide feed. When querying MTC Doorway for Santa Clara city listings, filter by `listingsBuildingAddress.city == "Santa Clara"` to avoid pulling San José records.

---

## HouseKeys platform

HouseKeys is a registration/notification/lottery portal. It is **not** a scrapeable list of units. The adapter returns one high-confidence registration record that directs users to the right subdomain.

HouseKeys city subdomains (confirmed as of 2026-06-05):
- housekeys1.com — Morgan Hill
- housekeys5.com — Gilroy
- housekeys7.com — Santa Clara (terminated as admin 2024-09-30; still handles Sofia/Prado/Lafayette resales)
- housekeys12.com — Los Gatos
- housekeys13.com — Mountain View
- housekeys24.com — Milpitas (reference instance)

The city-specific subdomain is read from the `Administrator URL` column in `TARGETS.md` and passed to `scrape_housekeys(authority, url, admin_url=admin_url)`. Do not hardcode Milpitas.

---

## Santa Clara city — admin transition (important)

- HouseKeys7 terminated as delegated admin 2024-09-30
- BMP homeownership now: Hello Housing (hellohousingsv.org)
- Rental listings (Monroe Commons, etc.): MTC Doorway / Bloom Housing at housingbayarea.mtc.ca.gov
- City website (santaclaraca.gov): WAF-blocked (Akamai hash `d7ce17`, same config as Mountain View city-site and Sunnyvale)
- TARGETS.md row updated to point at MTC Doorway with `city_filter="Santa Clara"`

---

## WAF-blocked cities

Three cities share an Akamai WAF configuration (customer hash `d7ce17`) that blocks all automated access including real-browser Playwright, curl with browser headers, and robots.txt fetches:

- **Mountain View city-site** — viable alternative: HouseKeys13 directly (already in TARGETS.md)
- **Santa Clara city-site** — viable alternative: MTC Doorway (already in TARGETS.md)
- **Sunnyvale** — no viable alternative found. Document IDs 364, 366, 368 confirmed 2026-06-05 but all served via docaccess.com which fetches from the blocked domain. Marked `waf_blocked`.

When you hit a WAF block: document it (what you tried, the specific block signature), find an alternative entry point if one exists, update TARGETS.md. Do not attempt to bypass.

---

## Routing logic (cli.py)

The dispatcher in `cli.py` tries, in order:
1. `extract_target()` from `extraction/__init__.py` — handles Bloom Housing domains and PDF/DocumentCenter links
2. GIS extraction (if `gis` in measures and administrator is set)
3. `waf_blocked` early-exit (logs WARN, skips to next target)
4. Platform-specific adapter routing based on URL/measures keywords
5. Generic scraper (last resort, noisy — avoid)

When adding a new adapter, add routing in step 4. If the platform can be detected reliably by URL or measure keyword, it belongs here. Keep conditions specific — the order matters and broad conditions shadow narrower ones below.

---

## Target ingestion safety

`registry.py` sanitizes every TARGETS.md row on ingestion:
- URL scheme validation (http/https only)
- Control character stripping
- Length limits on all text fields
- Measures normalization
- Basic prompt-injection detection for agent contexts

Bad rows are logged as warnings and skipped. `scripts/doctor.py --fix` validates the full pipeline from TARGETS.md through DB ingestion.

---

## Key files

| File | Purpose |
|---|---|
| `TARGETS.md` | Source of truth: all targets, measures, admin contacts, WAF notes |
| `housing_list_search/cli.py` | Main run loop and routing dispatcher |
| `housing_list_search/registry.py` | TARGETS.md → SQLite with sanitization |
| `housing_list_search/scraper.py` | `polite_get()` — the only approved HTTP entry point |
| `housing_list_search/extraction/bloom_housing.py` | Bloom Housing platform adapter |
| `housing_list_search/extraction/__init__.py` | High-quality extraction dispatcher |
| `housing_list_search/adapters/` | Platform adapters |
| `scripts/doctor.py` | Environment health check + --fix mode |
| `SOUL.md` | Mission and ethical guardrails |

---

## Extension pattern for new adapters

1. Create `housing_list_search/adapters/{platform_name}.py`
2. Module docstring must include: what the platform is, current design assumptions, Scope & Guardrails, extension guidance
3. One public entry point with a stable signature (look at any existing adapter)
4. Add routing in `cli.py` step 4
5. Add a row to `AGENTS.md` adapter table
6. Add a TARGETS.md row for the reference city with appropriate measures

Do not create city-named files. Do not create adapters for things already handled by existing platforms.
