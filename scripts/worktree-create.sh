#!/usr/bin/env bash
set -euo pipefail

if [ $# -ne 2 ]; then
  echo "Usage: $0 <ticket> <design|dev>"
  exit 1
fi

TICKET="$1"
PHASE="$2"

case "$PHASE" in
  design) BRANCH="feat/${TICKET}-design"; WT="../wt-${TICKET}-design" ;;
  dev)    BRANCH="feat/${TICKET}-dev";    WT="../wt-${TICKET}-dev" ;;
  *) echo "phase must be design|dev"; exit 1 ;;
esac

git fetch origin
if git show-ref --verify --quiet "refs/heads/$BRANCH"; then
  git worktree add "$WT" "$BRANCH"
else
  git worktree add -b "$BRANCH" "$WT"
fi

echo "Created worktree: $WT ($BRANCH)"
