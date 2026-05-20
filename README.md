# housing-list-search

**Modular Low/No-Income Housing Waitlist Aggregator**  
Built for Santa Clara County nonprofits. Portable to any county.

**v0.8.2** — Five first-class adapters (john_stewart, gis_extraction, housekeys, cdn, alta) + freshness metadata + registry guardrails + operational `no_public_list` handling. Clean daily runs via `python main.py --run` after `doctor --fix`.

## Quick Start
```bash
git clone https://github.com/joshuafielden43/housing-list-search.git
cd housing-list-search
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python scripts/doctor.py --fix     # Recommended after clone or TARGETS.md changes
python main.py --run               # Normal daily extraction
```

See `PROJECT_CONTRACT_v0.8.2.md` and `AGENTS.md` for architecture, extension patterns, and how to add new cities that use existing administrators (Housing Group, Alta, HouseKeys, etc.).
