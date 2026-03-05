#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <run_id>|--list [limit]"
  exit 1
fi

if [ "$1" = "--list" ]; then
  LIMIT="${2:-20}"
  python3 "$(dirname "$0")/workflow.py" list --limit "$LIMIT"
else
  python3 "$(dirname "$0")/workflow.py" status --run-id "$1"
fi
