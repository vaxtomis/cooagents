#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 3 ]; then
  echo "Usage: $0 <run_id> <req|design> <approved_by> [comment]"
  exit 1
fi

RUN_ID="$1"
GATE="$2"
BY="$3"
COMMENT="${4:-}"

python3 "$(dirname "$0")/workflow.py" approve --run-id "$RUN_ID" --gate "$GATE" --by "$BY" --comment "$COMMENT"
