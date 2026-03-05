#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 1 ]; then
  echo "Usage: $0 <ticket>"
  exit 1
fi

TICKET="$1"
SESSION="design-${TICKET}"
WT="../wt-${TICKET}-design"

tmux has-session -t "$SESSION" 2>/dev/null || tmux new-session -d -s "$SESSION" -c "$WT"
tmux send-keys -t "$SESSION" "claude" Enter

echo "Started tmux session: $SESSION"
