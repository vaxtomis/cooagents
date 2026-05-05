#!/usr/bin/env bash
set -euo pipefail

PYTHON_BIN="python3"
if command -v python3.11 >/dev/null 2>&1; then
  PYTHON_BIN="python3.11"
elif ! command -v python3 >/dev/null 2>&1; then
  PYTHON_BIN="python"
fi

exec "$PYTHON_BIN" scripts/deploy.py bootstrap "$@"
