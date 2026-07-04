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
  if [[ -d "$LOCK_DIR" ]]; then
    # Recover stale locks left after kill -9 (stock macOS has no flock).
    lock_mtime="$(stat -f %m "$LOCK_DIR" 2>/dev/null || stat -c %Y "$LOCK_DIR" 2>/dev/null || echo 0)"
    now_epoch="$(date +%s)"
    lock_age=$((now_epoch - lock_mtime))
    if (( lock_age > 7200 )); then
      echo "$(date -Iseconds) Removing stale run_daily lock (${lock_age}s old)" | tee -a "$LOG_FILE"
      rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR"
    fi
  fi
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

if ! python scripts/doctor.py --dry-run >>"$LOG_FILE" 2>&1; then
  echo "$(date -Iseconds) ERROR: doctor --dry-run failed; aborting run" | tee -a "$LOG_FILE"
  exit 1
fi

if ! python -c "from playwright.sync_api import sync_playwright" >>"$LOG_FILE" 2>&1; then
  echo "$(date -Iseconds) WARNING: Playwright import failed — browser adapters may fail" | tee -a "$LOG_FILE"
fi

{
  echo "=== Housing List Run started at $(date -Iseconds) ==="
  python main.py --run
  echo "=== Housing List Run finished at $(date -Iseconds) ==="
  echo "Output files in ${ROOT}:"
  ls -l *.csv *.md 2>/dev/null || echo "(no csv/md outputs yet)"
} 2>&1 | tee -a "$LOG_FILE"