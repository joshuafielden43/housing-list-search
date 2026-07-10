# Contributing to housing-list-search

Thanks for helping. This project exists to make affordable housing data accessible to nonprofits. Contributions that keep it accurate, reliable, and easy to hand off to the next person are what matter.

---

## Who has contributed

| Contributor | Role |
|---|---|
| Joshua Fielden | Project lead, architecture, Santa Clara County domain knowledge |
| Claude Sonnet (Anthropic) | Adapter development, reverse engineering, refactoring |
| OpenAI Codex | Early PDF extraction work (Gilroy, `codex_pdf.py` foundation) |
| Grok Code CLI (xAI) | Early integration and web conversation sessions |

---

## Ground rules

1. **Data accuracy matters more than code elegance.** A scraper that returns stale or wrong records harms real people. If you are unsure whether output is correct, say so explicitly in your PR.

2. **Name adapters after platforms, not cities.** `bloom_housing.py` handles San José and MTC Doorway. The next Bloom city costs zero new adapter files. See `AGENTS.md` for the full naming policy.

3. **Document why, not what.** Inline comments explain workarounds, hidden constraints, and things that would surprise a future reader. They do not narrate what the code does — the code does that.

4. **Respect robots.txt and rate limits.** `polite_get()` in `scraper.py` is the only approved HTTP entry point for external requests. Do not bypass it. The 3-second default delay is intentional.

5. **Leave breadcrumbs for the next bot or human.** If you hit a WAF block, document it with the specific hash/config you found. If you find a workaround, document why the workaround is needed. See the `waf_blocked` cities in `TARGETS.md` for the expected pattern.

---

## Development setup

Local operator / maintainer workflow (see `README.md` for daily `run_daily.sh` cron). Not packaged for public fork onboarding.

```bash
cd housing-list-search
uv venv && source .venv/bin/activate
uv pip install -r requirements-dev.txt   # or requirements-dev.lock for pinned installs
npm install   # Husky pre-commit / pre-push hooks (secrets, ruff, doctor, pytest)
playwright install chromium
python scripts/doctor.py --fix
```

Commit-time checks (same pattern as `agent-deep-research`):

- **pre-commit:** secrets guard → `ruff` on staged `.py` → `doctor --dry-run` → unit tests
- **pre-push:** full `npm run check` (lint + doctor + tests)

Override once if you know it's safe: `git commit --no-verify`

Run tests:

```bash
pytest tests/ -m "not integration"   # default — matches CI, no network
pytest tests/ -m integration         # live ground_truth (all adapter families)
HLS_GT_MODE=core pytest tests/ -m integration   # shorter core set only
```

---

## Making changes

### Adding support for a new city

1. Check `TARGETS.md` — the city may already be documented (possibly as `no_public_list` or `waf_blocked`).
2. If the city uses an existing platform (Bloom Housing, HouseKeys, Housing Group/CDN, Alta, GIS), just add a TARGETS.md row with the right URL and `scraping_measures`. No code needed.
3. If it's a new platform, create `housing_list_search/adapters/{platform_name}.py` following the module docstring and Scope & Guardrails pattern from any existing adapter. Add routing in `dispatch.py`. Update `AGENTS.md`.

### Fixing a bug or updating an adapter

- Reproduce the problem with a targeted test or a direct call to the adapter.
- Fix only the specific issue. Do not refactor surrounding code in the same PR.
- If the fix involves a site that changed its structure, update the relevant TARGETS.md notes and adapter comments to explain what changed and when.

### Commit style

```
type: short description under 72 chars

Optional body explaining the why — what constraint forced this approach,
what was tried and didn't work, what the site does that required this.

Co-Authored-By: Your Name <email>
```

Types: `feat` `fix` `docs` `chore` `refactor` `test`

Atomic commits — one logical change per commit. If a PR includes both a bug fix and a docs update, those are two commits.

### Pull requests

- Branch off `main`: `git checkout -b feat/your-description`
- PR title matches commit style: `fix: gilroy cdn adapter misses page 2`
- Fill out the PR description with: what changed, why, and how you verified it works
- CI runs `pytest tests/ -m "not integration"` automatically — PRs with failing unit tests will not be merged
- `main` is protected: you cannot push directly. All changes go through a PR, even solo work (this creates a permanent record of what changed and why)

---

## Things not to do

- Do not add city-specific adapter files (`cupertino.py`, `san_jose.py`). If you find yourself doing this, step back and identify the underlying platform pattern.
- Do not commit output files (`current_full.csv`, `daily_summary.md`, `*.db`, `snapshots/`). They are in `.gitignore`.
- Do not add error handling for situations that cannot happen. Trust internal guarantees; only validate at system boundaries.
- Do not add retry loops or `sleep()` calls outside of `polite_get()`.
- Do not skip `robots.txt`. If a site blocks it via WAF, document that fact and move on — do not assume permission.

---

## Questions

Open a GitHub issue. For context on why specific decisions were made, `git log --follow -p` on the relevant file usually explains it.
