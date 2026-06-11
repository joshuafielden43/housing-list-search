# AGENTS.md — Notes for AI Contributors and Future Maintainers

This file is the handoff document for AI systems (and humans) picking up this project. It explains the architecture, the naming conventions, the active adapters, and the patterns that have been established so you don't re-derive them from scratch.

Read this before writing any code. Read `SOUL.md` for the mission guardrails.

---

## Session friction note

Joshua dislikes constant permission approval prompts during active development.

**In Claude Code:** `/always-approve` enables auto-approval for the session. Use when Joshua says things like "fewer questions", "just do the work", "stop asking every time".

---

## Accuracy bar — United Effort Organization

**Benchmark:** [The United Effort Organization — Affordable Housing](https://www.theunitedeffort.org/housing/affordable-housing/)

UEO maintains a volunteer-curated county-wide database (**~560 properties** as of 2026-06) with per-property availability, unit types, contact info, and city filters. They do it by hand. Their known weakness is **stale data** — properties that are no longer available but have not been aged out of their database.

**Our goal:** Match or exceed their property coverage using only public, ethically accessible sources — and **beat them on freshness** via automated daily runs, `STALE` diff labelling, and `db_manage.py prune`.

**Current gap (honest, 2026-06-07):** `run_prev.csv` holds **~113 records**, heavily weighted toward San José Bloom (~88). John Stewart / SCCHA directory records are absent from the latest baseline. UEO lists individual properties across Campbell, Cupertino, Gilroy, Los Altos, Los Gatos, Milpitas, Morgan Hill, Mountain View, Palo Alto, San José, Santa Clara, Saratoga, and Sunnyvale — we mostly capture portal-level or registration-level rows for several of those cities, not property inventories.

**Do not close the gap by:**
- Fabricating "delegate" or "See administrator" placeholder records when a portal has no listings
- Bypassing documented WAF blocks (Sunnyvale city-site, etc.)
- Inflating counts with navigation noise, lender pages, or program-overview PDFs

**Do close the gap by:**
- Extracting real property-level records from each city's actual public source (Bloom SSR, John Stewart, GIS, Alta property pages, Gilroy `/797` availability list, Housing Group rentals pages when units are posted, etc.)
- Letting `no_public_list` and `waf_blocked` targets stay empty rather than faking coverage
- Using UEO as a **coverage checklist** during validation ("does our San José count include properties UEO lists from jscosccha.com, midpen, alta, etc.?") — not as a scrape target

Menlo Park and Half Moon Bay were removed from `TARGETS.md`; they are San Mateo County Housing Group clients, not Santa Clara County scope.

---

## Current state (v0.8.6, 2026-06-05)

Six first-class adapters, all named after the recurring **platform or vendor** (never the city):

| Adapter | Pattern | Reference city |
|---|---|---|
| `extraction/bloom_housing.py` | Bloom Housing Next.js — SSR + REST API + Playwright fallback | San José (SSR), MTC Doorway (API) |
| `adapters/john_stewart.py` | Vendor portal, custom front-end | SCCHA properties |
| `adapters/gis_extraction.py` | Municipal GIS layers + federated managers | Cupertino + Rise Housing |
| `adapters/housekeys.py` | Registration/notification/lottery portal | Morgan Hill, Gilroy, Los Gatos, Mountain View, Milpitas |
| `adapters/civicplus.py` | CivicPlus municipal CMS (DocumentCenter viewers, Froala blocks) behind CDN/WAF | Gilroy, Campbell, Los Altos, Los Gatos |
| `adapters/alta.py` | Alta Housing delegated administrator + property directory | Palo Alto, Mountain View |
| `adapters/john_stewart.py` (jsco.net mode) | Corporate portfolio via WordPress REST API | 67 county properties |
| `adapters/charities_housing.py` | Charities Housing find-a-home cards + WP REST API | ~48 county properties |
| `adapters/midpen.py` | MidPen county-filtered search (leasing statuses) | ~46 county properties |
| `adapters/eden.py` | Eden Housing county-filtered grid (statuses) | ~36 county properties |
| `adapters/eah.py` | EAH all-properties list, county filter | ~27 county properties |
| `adapters/first_housing.py` | First Community Housing Wix portfolio (contacts) | ~21 properties |

High-quality structured extraction lives in `extraction/`. Adapters in `adapters/` handle messier or registration-only cases. Routing lives in `runner.py` (not `cli.py`), driven entirely by `scraping_measures`.

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

### robots.txt gotcha (John Stewart / Cloudflare)

`scraper.is_allowed_by_robots()` must fetch `robots.txt` with our nonprofit `USER_AGENT` via `requests`, then `RobotFileParser.parse()` — **never** `RobotFileParser.read()`. The stdlib `read()` uses Python-urllib's default bot string; Cloudflare returns HTTP 403, which urllib interprets as `disallow_all=True` and blocks every URL on the host. John Stewart (`jscosccha.com`, `scchousingauthority.org`) looked WAF-blocked in runs while the actual pages are public and permissive. This regressed when robots enforcement landed in commit `725a87b`.

---

## Partial runs (--target)

`python main.py --run --target "Morgan Hill"` filters the active targets list to rows whose authority contains the needle (case-insensitive). All normal run outputs are produced for the matched targets only:

- `diff.csv` and `run_prev.csv` reflect only the matched-target results for that invocation — they are **not** a global DB diff. If you run `--target` followed by a full `--run`, `diff.csv` will correctly reflect the full-run delta on the next full run.
- Useful for rapid iteration on a single adapter without waiting for all 15 targets.

---

## Routing logic (runner.py)

`runner.run_target(target_row)` is the single dispatch function. It is driven entirely by `scraping_measures` — URL substrings and authority name patterns are explicitly not used. Order of operations:

1. `extract_target()` — handles Bloom Housing domains and PDF/DocumentCenter links. Results are collected but do **not** suppress named-measure adapters (a row can produce records from both Bloom and HouseKeys).
2. Named-measure adapters fire for every matching measure: `john_stewart`, `gis`, `housekeys`, `civicplus` (legacy alias: `cdn`), `alta`, `charities_housing`, `midpen`, `eden`, `eah`, `first_housing`. All matching adapters run; zero results from one does not suppress others.
3. `waf_blocked` — immediate empty return before any adapter or network call.
4. Playwright or generic-scrape fallback — only if no named measure fired.

Unknown measures produce a WARNING log so TARGETS.md typos surface immediately.

When adding a new adapter:
1. Add the measure name to `known` in `runner.py`
2. Add a module-level import guard at the top of `runner.py`
3. Add an `if "your_measure" in measures` block in section 2
4. Add routing documentation here

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
| `housing_list_search/runner.py` | Measure-driven dispatch — routes each target to adapter(s) |
| `housing_list_search/cli.py` | Main run loop: load targets → runner → dedupe → DB → CSV export |
| `housing_list_search/db.py` | DatabaseManager: upsert_listings, export_csv, export_diff_csv, prune |
| `housing_list_search/registry.py` | TARGETS.md → SQLite `targets` table (sole owner of that schema) |
| `housing_list_search/scraper.py` | `polite_get()` — the only approved HTTP entry point |
| `housing_list_search/extraction/bloom_housing.py` | Bloom Housing platform adapter |
| `housing_list_search/extraction/__init__.py` | Extraction layer dispatcher (Bloom domains, PDF links) |
| `housing_list_search/adapters/` | Platform adapters |
| `scripts/doctor.py` | Environment health check + --fix (re-ingests TARGETS.md) + --dry-run (CI) |
| `SOUL.md` | Mission and ethical guardrails |

## Output files (every --run)

| File | What it is | When to use |
|---|---|---|
| `current_full.csv` | Full DB snapshot — all ever-seen records | Complete view for reference; import baseline |
| `diff.csv` | Delta view: NEW / UPDATED / STALE per `run_id` | Incremental imports; drive upserts without re-processing everything |
| `run_prev.csv` | This run's listing set (written at end of run) | Used internally as changelog diff baseline; ignore in downstream tools |
| `daily_summary.md` | Human-readable open listings + skipped targets | Nonprofit staff, mailing list |
| `changelog_diffs.md/.csv` | Added / removed / status-changed vs last run | Audit trail, change notification |

`diff.csv` and `current_full.csv` serve different audiences. Use `diff.csv` when you want "what changed this run." Use `current_full.csv` when you want the full known inventory. `STALE` in `diff.csv` means a record exists in the DB but was not confirmed in the most recent run — it may have closed or been removed from the source. Prune stale records with `scripts/db_manage.py prune`.

`--run` logs a WARNING when STALE count ≥ 5 (`DEFAULT_STALE_WARN_THRESHOLD` in `db.py`). Living contract: `PROJECT_CONTRACT_v0.8.6.md`.

## Tests

- CI and default local runs: `pytest tests/ -m "not integration"`
- Live portal smoke tests: `pytest tests/ -m integration` (San José Bloom + Gilroy PDF)

---

## Pre-release checklist

Before tagging a release or merging a PR that changes TARGETS.md or adapters:

1. `python scripts/doctor.py --fix` — re-ingests TARGETS.md, re-runs sanitizer, smoke-tests adapter imports
2. `pytest tests/ -m "not integration"` — all unit tests pass
3. `python main.py --run --target "<one active city>"` — sanity check that the run loop produces output
4. Update `AGENTS.md` version line and commit

---

## Extension pattern for new adapters

1. Create `housing_list_search/adapters/{platform_name}.py`
2. Module docstring must include: what the platform is, current design assumptions, Scope & Guardrails, extension guidance
3. One public entry point with a stable signature (look at any existing adapter)
4. Add routing in `runner.py` (new `if "your_measure" in measures` block)
5. Add a row to `AGENTS.md` adapter table
6. Add a TARGETS.md row for the reference city with appropriate measures

Do not create city-named files. Do not create adapters for things already handled by existing platforms.
