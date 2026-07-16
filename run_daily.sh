#!/usr/bin/env bash
# run_daily.sh - For cron / Hermes scheduling
#
# Full daily scrape with strict error handling, .venv activation, overlap
# protection, and append-only logging.
#
# Exit codes:
#   0 — run completed successfully
#   1 — doctor/venv/main failure (scrape or preflight)
#   2 — lock skip: another run is active (or lock still recovering) (#247)
#   3 — lock holder was stale and force-recovered failed; manual cleanup needed

set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")" && pwd)"
cd "$ROOT"

# Hermes cron: env from ~/.hermes/.env (inherited). Local override: repo .env if present.
if [[ -f "${ROOT}/.env" ]]; then
  set -a
  # shellcheck source=/dev/null
  source "${ROOT}/.env"
  set +a
fi

LOCK_FILE="${ROOT}/.run_daily.lock"
LOCK_META="${ROOT}/.run_daily.lock.meta"
LOCK_DIR="${ROOT}/.run_daily.lock.d"
# Max age for a held lock before we treat the holder as hung (#247). Default 2h.
LOCK_MAX_AGE_S="${HLS_RUN_DAILY_LOCK_MAX_AGE_S:-7200}"
LOG_DIR="${ROOT}/logs"
LOG_FILE="${LOG_DIR}/run_$(date +%Y%m%d).log"
# 0 = flock path; 1 = mkdir fallback path (for EXIT trap cleanup)
_LOCK_KIND=0

mkdir -p "$LOG_DIR"

_log() {
  echo "$(date -Iseconds) $*" | tee -a "$LOG_FILE"
}

_write_lock_meta() {
  # pid start_epoch — used for dead-pid and max-age recovery
  echo "$$ $(date +%s)" >"$LOCK_META"
}

_read_lock_meta() {
  # Sets lock_pid and lock_start from meta file; empty if unreadable.
  lock_pid=""
  lock_start=0
  if [[ -f "$LOCK_META" ]]; then
    # shellcheck disable=SC2034
    read -r lock_pid lock_start <"$LOCK_META" || true
    lock_start="${lock_start:-0}"
  fi
}

_clear_lock_meta() {
  rm -f "$LOCK_META" 2>/dev/null || true
}

_pid_alive() {
  local pid="$1"
  [[ -n "$pid" ]] && kill -0 "$pid" 2>/dev/null
}

_force_release_stale_holder() {
  # Called when lock is held too long or holder PID is dead. Best-effort reclaim.
  _read_lock_meta
  local now_epoch age
  now_epoch="$(date +%s)"
  age=$((now_epoch - lock_start))

  if [[ -n "${lock_pid:-}" ]] && ! _pid_alive "$lock_pid"; then
    _log "Removing stale run_daily lock — holder pid ${lock_pid} is dead"
    _clear_lock_meta
    rm -f "$LOCK_FILE" 2>/dev/null || true
    rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR" 2>/dev/null || true
    return 0
  fi

  if (( age > LOCK_MAX_AGE_S )) && [[ -n "${lock_pid:-}" ]] && _pid_alive "$lock_pid"; then
    _log "Stale run_daily lock age=${age}s (max=${LOCK_MAX_AGE_S}s) — terminating hung holder pid ${lock_pid}"
    kill -TERM "$lock_pid" 2>/dev/null || true
    sleep 2
    if _pid_alive "$lock_pid"; then
      kill -KILL "$lock_pid" 2>/dev/null || true
      sleep 1
    fi
    _clear_lock_meta
    rm -f "$LOCK_FILE" 2>/dev/null || true
    rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR" 2>/dev/null || true
    return 0
  fi

  if (( age > LOCK_MAX_AGE_S )); then
    # Meta missing or unusable but lock file is ancient — clear paths.
    _log "Removing stale run_daily lock artifacts (age=${age}s or unknown holder)"
    _clear_lock_meta
    rm -f "$LOCK_FILE" 2>/dev/null || true
    rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR" 2>/dev/null || true
    return 0
  fi

  return 1
}

_release_lock() {
  _clear_lock_meta
  if (( _LOCK_KIND == 1 )); then
    rmdir "$LOCK_DIR" 2>/dev/null || true
  fi
  # flock FD 9 closes on process exit automatically
}

_acquire_lock() {
  if command -v flock >/dev/null 2>&1; then
    _LOCK_KIND=0
    exec 9>"$LOCK_FILE"
    if flock -n 9; then
      _write_lock_meta
      trap '_release_lock' EXIT INT TERM
      return 0
    fi
    # Contended — try stale recovery then one retry
    if _force_release_stale_holder; then
      exec 9>"$LOCK_FILE"
      if flock -n 9; then
        _write_lock_meta
        trap '_release_lock' EXIT INT TERM
        _log "Acquired run_daily lock after stale recovery"
        return 0
      fi
      _log "ERROR: stale lock recovery did not free flock; manual cleanup of ${LOCK_FILE} may be needed"
      return 3
    fi
    return 1
  fi

  # Portable fallback when flock is unavailable (e.g. stock macOS).
  _LOCK_KIND=1
  if [[ -d "$LOCK_DIR" ]]; then
    if _force_release_stale_holder; then
      :
    else
      # Age check without meta (legacy locks)
      lock_mtime="$(stat -f %m "$LOCK_DIR" 2>/dev/null || stat -c %Y "$LOCK_DIR" 2>/dev/null || echo 0)"
      now_epoch="$(date +%s)"
      lock_age=$((now_epoch - lock_mtime))
      if (( lock_age > LOCK_MAX_AGE_S )); then
        _log "Removing stale run_daily lock.d (${lock_age}s old)"
        rmdir "$LOCK_DIR" 2>/dev/null || rm -rf "$LOCK_DIR"
      fi
    fi
  fi
  if ! mkdir "$LOCK_DIR" 2>/dev/null; then
    return 1
  fi
  _write_lock_meta
  trap '_release_lock' EXIT INT TERM
  return 0
}

acq_rc=0
_acquire_lock || acq_rc=$?
if (( acq_rc != 0 )); then
  if (( acq_rc == 3 )); then
    _log "ERROR: could not reclaim run_daily lock; exiting 3"
    exit 3
  fi
  _log "Another run_daily.sh is already running (lock skip); exiting 2"
  exit 2
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

# ADR-0005 / #1089: daily scrape must never auto-load multi-GB marker OCR.
# Opt-in OCR is a separate job with HLS_ENABLE_MARKER_PDF=1 on a capable host.
export HLS_DISABLE_MARKER_PDF=1
# Ensure enable cannot ride in from a polluted environment on the cron host.
unset HLS_ENABLE_MARKER_PDF || true

if ! python scripts/doctor.py --dry-run >>"$LOG_FILE" 2>&1; then
  echo "$(date -Iseconds) ERROR: doctor --dry-run failed; aborting run" | tee -a "$LOG_FILE"
  exit 1
fi

if ! python -c "from playwright.sync_api import sync_playwright" >>"$LOG_FILE" 2>&1; then
  echo "$(date -Iseconds) WARNING: Playwright import failed — browser adapters may fail" | tee -a "$LOG_FILE"
fi

{
  echo "=== Housing List Run started at $(date -Iseconds) ==="
  echo "HLS_DISABLE_MARKER_PDF=${HLS_DISABLE_MARKER_PDF:-} (daily posture: OCR dark)"
  echo "LOCK_MAX_AGE_S=${LOCK_MAX_AGE_S} (stale lock reclaim budget; #247)"
  python main.py --run
  echo "=== Housing List Run finished at $(date -Iseconds) ==="
  echo "Output files in ${ROOT}:"
  ls -l *.csv *.md 2>/dev/null || echo "(no csv/md outputs yet)"
} 2>&1 | tee -a "$LOG_FILE"
# tee succeeds even when main.py fails — propagate the run exit code for cron/Hermes.
run_exit="${PIPESTATUS[0]:-0}"
if (( run_exit != 0 )); then
  exit "$run_exit"
fi
