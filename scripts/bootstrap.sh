#!/usr/bin/env bash
set -euo pipefail

echo "=== cooagents bootstrap ==="

# ---------- 1. Check Python >=3.11 ----------
python3 --version 2>/dev/null || { echo "ERROR: python3 not found"; exit 1; }
PYVER=$(python3 -c "import sys; v=sys.version_info; print(f'{v.major}.{v.minor}')")
PYOK=$(python3 -c "import sys; print(int(sys.version_info >= (3, 11)))")
if [ "$PYOK" != "1" ]; then
  echo "ERROR: Python >=3.11 required, got $PYVER"
  exit 1
fi
echo "Python $PYVER  ✓"

# ---------- 2. Check git ----------
git --version >/dev/null 2>&1 || { echo "ERROR: git not found"; exit 1; }
echo "git  ✓"

# ---------- 3. Check node / npm ----------
node --version >/dev/null 2>&1 || { echo "ERROR: node not found (required for acpx and web build)"; exit 1; }
npm --version >/dev/null 2>&1 || { echo "ERROR: npm not found (required for web build)"; exit 1; }
echo "node $(node --version)  ✓"
echo "npm $(npm --version)  ✓"

# ---------- 4. Check / install acpx ----------
if acpx --version >/dev/null 2>&1; then
  echo "acpx  ✓"
else
  echo "Installing acpx..."
  npm install -g acpx@latest
  acpx --version >/dev/null 2>&1 || { echo "ERROR: acpx install failed"; exit 1; }
  echo "acpx  ✓"
fi

# ---------- 5. Install Python dependencies (venv) ----------
echo "Installing dependencies..."
if python3 -m venv .venv 2>/dev/null; then
  # shellcheck disable=SC1091
  source .venv/bin/activate 2>/dev/null || source .venv/Scripts/activate 2>/dev/null
  pip install -r requirements.txt
  echo "venv + deps  ✓"
else
  echo "WARN: venv creation failed, falling back to global pip"
  pip install -r requirements.txt
  echo "deps (global)  ✓"
fi

# ---------- 6. Build web dashboard ----------
echo "Building web dashboard..."
[ -f web/package.json ] || { echo "ERROR: web/package.json not found"; exit 1; }
[ -f web/package-lock.json ] || { echo "ERROR: web/package-lock.json not found"; exit 1; }
(
  cd web
  npm ci
  npm run build
)
[ -f web/dist/index.html ] || { echo "ERROR: web build did not produce web/dist/index.html"; exit 1; }
echo "web dashboard  ✓"

# ---------- 7. Create runtime directories ----------
mkdir -p .coop/runs .coop/jobs
echo "runtime dirs  ✓"

# ---------- 8. Initialize database ----------
python3 -c "
import sqlite3, pathlib
db_path = '.coop/state.db'
backup = db_path + '.bak'
p = pathlib.Path(db_path)
if p.exists():
    import shutil
    shutil.copy2(db_path, backup)
    print(f'  Backed up existing DB to {backup}')
conn = sqlite3.connect(db_path)
conn.executescript(pathlib.Path('db/schema.sql').read_text())
conn.close()
print('  Database initialized.')
"
echo "database  ✓"

echo ""
echo "=== Bootstrap complete ==="
echo "Dashboard available at:"
echo "  http://127.0.0.1:8321/"
echo ""
echo "Start the server with:"
echo "  uvicorn src.app:app --host 127.0.0.1 --port 8321"
echo ""
