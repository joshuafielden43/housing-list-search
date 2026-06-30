#!/usr/bin/env bash
# run_daily.sh - For cron / Hermes scheduling
#
# Full daily scrape with strict error handling, .venv activation, overlap
# protection, and append-only logging.

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

LOCK_FILE="${ROOT}/.run_daily.lock"
LOG_DIR="${ROOT}/logs"
LOG_FILE="${LOG_DIR}/run_$(date +%Y%m%d).log"

mkdir -p "$LOG_DIR"

_acquire_lock() {
  if command -v flock >/dev/null 2>&1; then
    exec 9>"$LOCK_FILE"
    flock -n 9
    return
  fi

  # Portable fallback when flock is unavailable (e.g. stock macOS).
  LOCK_DIR="${ROOT}/.run_daily.lock.d"
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    return 1
  fi
  trap 'rmdir "$LOCK_DIR" 2>/dev/null || true' EXIT INT TERM
  return 0
}

if ! _acquire_lock; then
  echo "$(date -Iseconds) Another run_daily.sh is already running; exiting." | tee -a "$LOG_FILE"
  exit 1
fi

if [[ ! -f "${ROOT}/.venv/bin/activate" ]]; then
  {
    echo "$(date -Iseconds) ERROR: .venv not found at ${ROOT}/.venv"
    echo "Create it with: uv venv && source .venv/bin/activate && uv pip install -r requirements.txt"
  } | tee -a "$LOG_FILE"
  exit 1
fi
# shellcheck source=/dev/null
source "${ROOT}/.venv/bin/activate"

{
  echo "=== Housing List Run started at $(date -Iseconds) ==="
  python main.py --run
  echo "=== Housing List Run finished at $(date -Iseconds) ==="
  echo "Output files in ${ROOT}:"
  ls -l *.csv *.md 2>/dev/null || echo "(no csv/md outputs yet)"
} 2>&1 | tee -a "$LOG_FILE"