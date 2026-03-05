#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 2 ]; then
  echo "Usage: $0 <run_id> <by> [note]"
  exit 1
fi

RUN_ID="$1"
BY="$2"
NOTE="${3:-}"

python3 "$(dirname "$0")/workflow.py" retry --run-id "$RUN_ID" --by "$BY" --note "$NOTE"
