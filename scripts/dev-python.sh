#!/usr/bin/env bash
# Resolve project Python: prefer .venv, fall back to python3 on PATH.
set -euo pipefail
ROOT="$(cd "$(dirname "$0")/.." && pwd)"
if [ -x "$ROOT/.venv/bin/python" ]; then
  exec "$ROOT/.venv/bin/python" "$@"
fi
exec python3 "$@"