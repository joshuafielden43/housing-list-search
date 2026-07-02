#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
export HLS_DISABLE_MARKER_PDF=1
"$ROOT/scripts/dev-ruff.sh" check .
"$ROOT/scripts/dev-python.sh" scripts/doctor.py --dry-run