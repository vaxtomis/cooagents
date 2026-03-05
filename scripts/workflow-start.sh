#!/usr/bin/env bash
set -euo pipefail

if [ $# -lt 1 ]; then
  echo "Usage: $0 <ticket> [repo_path]"
  exit 1
fi

TICKET="$1"
REPO="${2:-$(cd "$(dirname "$0")/.." && pwd)}"

python3 "$(dirname "$0")/workflow.py" start --ticket "$TICKET" --repo "$REPO"
