# housing-list-search

**Modular Low/No-Income Housing Waitlist Aggregator**  
Built for Santa Clara County nonprofits. Portable to any county.

## Quick Start
```bash
git clone https://github.com/joshuafielden43/housing-list-search.git
cd housing-list-search
python3 -m venv venv
source venv/bin/activate
pip install -r requirements.txt
python main.py --discover   # First time only
./run_daily.sh              # Normal run
