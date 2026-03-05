#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <run_id>"
  exit 1
fi

python3 "$(dirname "$0")/workflow.py" tick --run-id "$1"
