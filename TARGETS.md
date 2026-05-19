# TARGETS.md – Housing Waitlist Targets
County: Santa Clara, CA
Last Discovery: 2026-05-19
Scope: controlled (seed mode)

## Instructions
Human review required. Edit freely. Scraping Measures guide the adapter (native_requests, playwright, etc.).

City/Authority | URL | Notes | Scraping Measures | Priority | Last Seen
---|---|---|---|---|---
Santa Clara County Housing Authority (SCCHA) | https://www.scchousingauthority.org/ | Online Interest Lists + property-specific. Portal: https://portal.scchousingauthority.org | playwright, rentcafe_portal, robots_respect | High | 2026-05-19
John Stewart Company (SCCHA properties) | https://jscosccha.com/ | Property waitlists & lotteries | native_requests, table_based | High | 2026-05-19
City of San José Affordable Housing Portal | https://housing.sanjoseca.gov/ | Current accepting applications + map | playwright, js_dynamic | High | 2026-05-19
Campbell | https://www.campbellca.gov/635/Below-Market-Rate-Program | BMR program | native_requests | Medium |
