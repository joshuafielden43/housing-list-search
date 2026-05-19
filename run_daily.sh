#!/bin/bash
# run_daily.sh - For cron / Hermes scheduling

cd "$(dirname "$0")"
source venv/bin/activate 2>/dev/null || true

echo "=== Housing List Run at $(date) ==="
python main.py --run

echo "Files ready in $(pwd)"
ls -l *.csv *.md
