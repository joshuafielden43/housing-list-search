#!/usr/bin/env bash
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"
"$ROOT/scripts/check-fast.sh"
export HLS_DISABLE_MARKER_PDF=1
"$ROOT/scripts/dev-python.sh" -m pytest tests/ -m "not integration" -q --tb=short