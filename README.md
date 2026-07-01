# housing-list-search

**Modular affordable-housing waitlist aggregator for Santa Clara County — portable to any county.**

Built for nonprofits that need a daily, structured picture of what low/no-income housing is open, closing, or on waitlist across a fragmented landscape of city portals, delegated administrators, and vendor platforms.

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

## Quick start

```bash
git clone https://github.com/joshuafielden43/housing-list-search.git
cd housing-list-search
uv venv && source .venv/bin/activate
uv pip install -r requirements.txt
playwright install chromium          # only needed for Playwright fallback paths
python scripts/doctor.py --fix      # validates environment + TARGETS.md ingestion
python main.py --run                 # normal daily extraction
```

Outputs land in the repo root: `current_full.csv`, `diff.csv`, `daily_summary.md`, `changelog_diffs.md`.

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

## Branch / contribution discipline

- `main` is protected: force-pushes and branch deletion are blocked (GitHub branch protection, enforced for admins too). Feature work happens on branches and lands via PR.
- PR titles follow `type: short description` — `feat:`, `fix:`, `docs:`, `chore:`.
- Commits in PRs should be atomic and have a subject line under 72 characters.
- CI runs unit tests only (`pytest -m "not integration"`). Live portal tests are opt-in: `pytest -m integration`.
- After each `--run`, check `diff.csv` for `STALE` rows; when the count is high, prune with `scripts/db_manage.py prune`.
- `python main.py --run --target "City Name"` is a partial diagnostic run: `diff.csv` is scoped to the selected authority, `run_prev.csv` is not updated, staff-facing `daily_summary.md` is preserved, and a diagnostic `daily_summary_partial.md` is written instead.
- See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

---

## License

MIT — see [LICENSE](LICENSE).

Built by Joshua Fielden with Claude Sonnet (Anthropic), OpenAI Codex, and Grok Code CLI (xAI).
