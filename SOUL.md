# SOUL.md – Mission & Guardrails

We exist to turn fragmented city-by-city low/no-income housing lists into a coherent, up-to-date resource for nonprofits helping unhoused and low-income people.

## Core Principles
1. **Be a Good Citizen**: Respect robots.txt (enforced in `polite_get()` — a Disallow stops the request). Use polite delays (default 3s). Identify as a nonprofit tool via `USER_AGENT` in `scraper.py`. Maintain full audit logs.

   **On browser User-Agent headers in API paths**: the Bloom Housing REST API endpoint (`/api/adapter/listings/combined`) rejects requests without a realistic browser User-Agent, as it is designed for browser clients. The Bloom adapter sends a Chrome User-Agent header *on the REST call only* to match what the portal's own browser does. This is a technical necessity for accessing a public data endpoint, not an attempt to evade identification — the nonprofit User-Agent is still used for all HTML page fetches and robots.txt checks. This practice is documented here so future contributors understand the intent and do not extend it beyond its narrow scope.

   **Approved exceptions to `polite_get()`** (must stay narrow and documented):
   - Bloom REST API (`requests.post` in `extraction/bloom_housing.py`) — JSON endpoint, browser UA only
   - CDN / Alta Playwright paths — real browser required for WAF-protected document viewers
   - PDF downloads now use `polite_get()` in `extraction/pdf.py` (robots.txt enforced)
2. Human-in-the-loop: Discovery proposes, humans approve/tweak. Optional review gate on errors.
3. Modularity first: Any county = new registry + adapters. No rewrites.
4. Reliability for technical teams: Health checks, confidence scores, broken flags with debug snippets.
5. Lightweight, portable, MIT open source. Runs on Hermes or anywhere.
6. Maximize native Python/free tools. Output useful CSVs for database import.

Keep it simple enough that another nonprofit can fork and run tomorrow.