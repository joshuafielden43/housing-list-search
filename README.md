# housing-list-search

**Modular affordable-housing waitlist aggregator for Santa Clara County — portable to any county.**

Built for nonprofits that need a daily, structured picture of what low/no-income housing is open, closing, or on waitlist across a fragmented landscape of city portals, delegated administrators, and vendor platforms.

Current scope: **17 Santa Clara County targets** across every city and the county housing authority.  
Current version: **v0.8.6**

---

## What it does

- Runs a daily scrape against every target in `TARGETS.md`
- Produces `current_full.csv` (full DB snapshot), `diff.csv` (NEW/UPDATED/STALE delta for importers), `daily_summary.md` (human-readable open listings), and `changelog_diffs.csv` (run-to-run change log)
- Handles six distinct backend patterns with purpose-built adapters (see [Adapter Map](#adapter-map))
- Skips WAF-blocked and no-public-list cities cleanly with documented rationale instead of crashing or returning junk
- Deduplicates across overlapping sources (e.g. San José portal + SCCHA directory)

---

## Quick start

```bash
git clone https://github.com/joshuafielden43/housing-list-search.git
cd housing-list-search
python3 -m venv venv && source venv/bin/activate
pip install -r requirements.txt
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
| `bloom_housing.py` | Bloom Housing (Next.js) | San José (SSR), MTC Doorway/Bay Area (REST API) |
| `john_stewart.py` | John Stewart Company portal | SCCHA-managed properties |
| `gis_extraction.py` | Municipal GIS layers | Cupertino + Rise Housing |
| `housekeys.py` | HouseKeys registration portal | Morgan Hill, Gilroy, Los Gatos, Mountain View, Milpitas, (Santa Clara transitional) |
| `cdn.py` | CDN/WAF-protected document viewers | Campbell, Los Altos, Menlo Park, Half Moon Bay (Housing Group); Gilroy PDFs |
| `alta.py` | Alta Housing portal | Palo Alto |

Three cities (Mountain View city-site, Santa Clara city-site, Sunnyvale) sit behind Akamai WAF and are documented as `waf_blocked` in `TARGETS.md`. Mountain View and Santa Clara have viable alternative entry points (HouseKeys subdomain and MTC Doorway respectively). Sunnyvale's document viewer also fetches from the blocked domain; documented with correct document IDs for when the block resolves.

---

## Adding a new city

**If it uses an existing platform:** add a row to `TARGETS.md` with the platform's URL and the correct `scraping_measures` value (e.g. `housekeys`, `cdn`, `native_requests`). No code changes needed.

**If it's a new Bloom Housing instance:** add the hostname to `_KNOWN_BLOOM_DOMAINS` in `extraction/__init__.py`. If it's a CSR/API instance, also add it to `_API_INSTANCES` in `extraction/bloom_housing.py`. No adapter code needed.

**If it's a genuinely new platform:** create a new adapter in `housing_list_search/adapters/`, name it after the platform, follow the module docstring and Scope & Guardrails pattern in any existing adapter, and add routing in `runner.py`. Document the pattern in `AGENTS.md`.

---

## Repo layout

```
housing_list_search/
  adapters/          # First-class platform adapters (bloom_housing, housekeys, cdn, …)
  extraction/        # Structured extraction layer (bloom_housing, pdf)
  runner.py          # Measure-driven target dispatcher (routes each TARGETS.md row)
  db.py              # DatabaseManager: upsert, export_csv, export_diff_csv, prune
  scraper.py         # polite_get() — rate-limited, robots.txt-respecting HTTP
  registry.py        # TARGETS.md → SQLite targets table with sanitization nanny
  cli.py             # Main run loop: load → runner → dedupe → DB → CSV export
scripts/
  doctor.py          # Environment health check + --fix + --dry-run (CI)
TARGETS.md           # Source of truth: all targets, measures, admin contacts
SOUL.md              # Mission and guardrails
AGENTS.md            # Notes for AI contributors and future maintainers
PROJECT_CONTRACT_v0.8.6.md  # Living contract (daily run, outputs, responsibility split)
```

---

## Branch / contribution discipline

- `main` is protected: direct pushes are blocked; changes go through PRs.
- PR titles follow `type: short description` — `feat:`, `fix:`, `docs:`, `chore:`.
- Commits in PRs should be atomic and have a subject line under 72 characters.
- CI runs unit tests only (`pytest -m "not integration"`). Live portal tests are opt-in: `pytest -m integration`.
- After each `--run`, check `diff.csv` for `STALE` rows; when the count is high, prune with `scripts/db_manage.py prune`.
- See [CONTRIBUTING.md](CONTRIBUTING.md) for the full contributor guide.

---

## License

MIT — see [LICENSE](LICENSE).

Built by Joshua Fielden with Claude Sonnet (Anthropic), OpenAI Codex, and Grok Code CLI (xAI).
