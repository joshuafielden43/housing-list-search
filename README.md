# housing-list-search

**Daily affordable-housing inventory scraper for Santa Clara County.**

Built to produce a structured, fresh picture of what low/no-income housing is open, closing, or on waitlist across city portals, delegated administrators, and vendor platforms. **Local-first:** runs on a single operator machine (Hermes/cron via `run_daily.sh`), not a public release or multi-tenant service.

Current scope: **24 Santa Clara County targets** in `TARGETS.md` (20 active scrape targets; 4 `no_public_list` monitors).  
Current version: **v0.8.7**

---

## What it does

- Runs a daily scrape against every target in `TARGETS.md`
- Produces `current_full.csv` (full DB snapshot), `diff.csv` (NEW/UPDATED/STALE delta for importers), `daily_summary.md` (human-readable open listings), and `changelog_diffs.csv` (run-to-run change log)
- Routes **12 platform adapters** via measure-driven dispatch (`dispatch.py`) plus URL extractors for Bloom and PDF (see [Adapter Map](#adapter-map))
- Skips `no_public_list` monitors and documented WAF blocks cleanly; uses alternative entry points where available (HouseKeys, MTC Doorway, Sunnyvale GIS)
- Deduplicates across overlapping sources (e.g. San José portal + SCCHA directory)

---

## Operator setup (local / Hermes)

```bash
cd housing-list-search
uv venv && source .venv/bin/activate
uv pip install -r requirements-dev.txt   # prod: requirements.txt; dev adds pytest + ruff
playwright install chromium              # Playwright adapter paths
python scripts/doctor.py --fix           # env check + TARGETS.md → SQLite
python main.py --run                     # full daily scrape
# or cron:
./run_daily.sh                           # doctor preflight, lock, logs under logs/
```

**After each run:** read `daily_summary.md` (staff) and `diff.csv` (machine delta). Check **Needs Review** when suspicious-zero or reverification-due signals fire.

Optional alerts: copy `.env.example` → `.env` (gitignored). `run_daily.sh` sources `.env` automatically.

- `HLS_NEEDS_REVIEW_WEBHOOK` — JSON POST to Hermes/n8n
- `HLS_VIKUNJA_URL` + `HLS_VIKUNJA_TOKEN` — create/update `[Reverify] {authority}` tasks in Vikunja project **#9**

**Outputs** (gitignored runtime artifacts in repo root): `current_full.csv`, `diff.csv`, `daily_summary.md`, `changelog_diffs.md`, `housing_registry.db`.

**Checkpoints:** `python scripts/db_manage.py snapshot --name <label>` archives DB + CSV under `snapshots/` (local only — not committed).

**Quality gate:** `npm run check` (ruff + doctor dry-run + unit tests).

---

## Adapter map

Adapters are named after the **platform or vendor**, never the city. The same adapter covers every city that uses that backend.

| Adapter | Platform | Cities / Use cases |
|---|---|---|
| `extraction/bloom_housing.py` | Bloom Housing (Next.js) | San José (SSR), MTC Doorway/Bay Area (REST API) |
| `adapters/john_stewart.py` | John Stewart Company portal + jsco.net API | SCCHA directory, jscosccha.com, county portfolio |
| `adapters/gis_extraction.py` | Municipal GIS layers | Cupertino (Rise Housing), Sunnyvale affordable-housing layer |
| `adapters/housekeys.py` | HouseKeys registration portal | Morgan Hill, Gilroy, Los Gatos, Mountain View, Milpitas |
| `adapters/civicplus.py` | CivicPlus municipal CMS (DocumentCenter, Froala) | Campbell, Los Altos (Housing Group); Gilroy PDFs |
| `adapters/alta.py` | Alta Housing portal + property directory | Palo Alto, Mountain View |
| `adapters/charities_housing.py` | Charities Housing directory + REST API | Santa Clara County |
| `adapters/midpen.py` | MidPen Housing county search (waitlist statuses) | Santa Clara County |
| `adapters/eden.py` | Eden Housing county property grid | Santa Clara County |
| `adapters/eah.py` | EAH Housing all-properties list | Santa Clara County |
| `adapters/first_housing.py` | First Community Housing portfolio (contacts) | San José |
| `extraction/pdf.py` | PDF extraction (tables, flyers, optional marker fallback) | Gilroy rental lists, Los Gatos guide |

**WAF note:** Mountain View and Santa Clara **city websites** sit behind Akamai WAF (`d7ce17`). Scraping uses HouseKeys13 and MTC Doorway instead. Sunnyvale's main site is also WAF-blocked, but its **ArcGIS REST layer** (`gis.sunnyvale.ca.gov`) is publicly queryable — handled by `gis_extraction`.

---

## Adding a new city

**If it uses an existing platform:** add a row to `TARGETS.md` with the platform's URL and the correct `scraping_measures` value (e.g. `housekeys`, `civicplus`, `native_requests`). No code changes needed.

**If it's a new Bloom Housing instance:** add the hostname to `BLOOM_DOMAINS` in `extraction/bloom_housing.py` and the `bloom` measure to the TARGETS.md row. For CSR/API instances, also add to `_API_INSTANCES`.

**If it's a genuinely new platform:** create `housing_list_search/adapters/{platform}.py`, register the measure in `dispatch.py`, and add a TARGETS.md row. See `AGENTS.md` for the full extension pattern.

---

## Repo layout

```
housing_list_search/
  adapters/          # Platform adapters (housekeys, civicplus, john_stewart, …)
  extraction/        # Structured extraction (bloom_housing, pdf, marker_pdf)
  dispatch.py        # Measure registry + URL extractors (bloom, pdf)
  pipeline.py        # Run orchestration: scrape → dedupe → persist → export
  runner.py          # Thin wrapper: run_target() → dispatch
  listing.py         # Canonical listing_to_row() at persistence seam
  freshness.py       # Unified diff.csv ↔ changelog identity
  db.py              # DatabaseManager: upsert, export_csv, export_diff_csv, prune
  scraper.py         # polite_get() — rate-limited, robots.txt-respecting HTTP
  registry.py        # TARGETS.md → SQLite targets table with sanitization nanny
  cli.py             # Argparse + RunPipeline entry point
scripts/
  doctor.py          # Environment health check + --fix + --dry-run (CI)
TARGETS.md           # Source of truth: all targets, measures, admin contacts
SOUL.md              # Mission and guardrails
AGENTS.md            # Notes for AI contributors and future maintainers
.agents/MEMORY.md    # Repo-local agent memory (Vikunja project #9, sprint notes)
PROJECT_CONTRACT_v0.8.6.md  # Living contract (daily run, outputs, responsibility split)
```

---

## Run discipline

- After each `--run`, check `diff.csv` for `STALE` rows; when the count is high, prune with `scripts/db_manage.py prune`.
- `python main.py --run --target "City Name"` is a partial diagnostic run: `diff.csv` scoped to matched authorities; `run_prev.csv` and staff `daily_summary.md` unchanged; writes `daily_summary_partial.md`.
- CI runs unit tests only (`pytest -m "not integration"`). Weekly live smoke: `.github/workflows/integration-weekly.yml`.
- See [AGENTS.md](AGENTS.md) for architecture handoff and [CONTRIBUTING.md](CONTRIBUTING.md) if you change adapters.

---

## License

MIT — see [LICENSE](LICENSE).

Built by Joshua Fielden with Claude Sonnet (Anthropic), OpenAI Codex, and Grok Code CLI (xAI).
