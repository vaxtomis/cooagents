#!/usr/bin/env bash
set -euo pipefail

ROOT="$(cd "$(dirname "$0")/.." && pwd)"
cd "$ROOT"

need_cmd() {
  command -v "$1" >/dev/null 2>&1 || {
    echo "[ERROR] missing command: $1"
    exit 1
  }
}

echo "[1/5] checking dependencies..."
need_cmd git
need_cmd python3
need_cmd tmux

if ! command -v claude >/dev/null 2>&1; then
  echo "[WARN] claude command not found (design stage tmux starter will fail until installed)"
fi
if ! command -v codex >/dev/null 2>&1; then
  echo "[WARN] codex command not found (dev stage tmux starter will fail until installed)"
fi

echo "[2/5] preparing runtime dirs..."
mkdir -p .coop/runs db scripts docs

echo "[3/5] initializing sqlite schema..."
python3 - <<'PY'
import pathlib, sqlite3
root = pathlib.Path('.').resolve()
db = root / '.coop' / 'state.db'
schema = (root / 'db' / 'schema.sql').read_text(encoding='utf-8')
conn = sqlite3.connect(db)
conn.executescript(schema)
conn.commit()
conn.close()
print('schema initialized:', db)
PY

echo "[4/5] setting executable bits..."
chmod +x scripts/*.sh scripts/*.py || true

echo "[5/5] done"
echo "Bootstrap complete."
echo "Quick start:"
echo "  scripts/workflow-start.sh <ticket>"
echo "  scripts/workflow-tick.sh <run_id>"
echo "  scripts/workflow-approve.sh <run_id> req <you> \"ok\""
echo "  scripts/workflow-approve.sh <run_id> design <you> \"ok\""
