#!/usr/bin/env bash
# Resolve ruff: prefer .venv install, fall back to PATH.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -x "$ROOT/.venv/bin/ruff" ]; then
  exec "$ROOT/.venv/bin/ruff" "$@"
fi
exec ruff "$@"