#!/usr/bin/env bash
set -euo pipefail

echo "=== cooagents bootstrap ==="

# Check Python version
python3 --version 2>/dev/null || { echo "ERROR: python3 not found"; exit 1; }
PYVER=$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')")
echo "Python version: $PYVER"

# Check git
git --version || { echo "ERROR: git not found"; exit 1; }

# Install dependencies
echo "Installing dependencies..."
pip install -r requirements.txt

# Create runtime directories
echo "Creating runtime directories..."
mkdir -p .coop/runs .coop/jobs

# Initialize database
echo "Initializing database..."
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

echo ""
echo "=== Bootstrap complete ==="
echo "Start the server with:"
echo "  uvicorn src.app:app --host 127.0.0.1 --port 8321"
echo ""
