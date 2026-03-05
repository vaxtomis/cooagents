#!/usr/bin/env python3
import pathlib
import sqlite3
import subprocess
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
DB = ROOT / ".coop" / "state.db"
WORKFLOW = ROOT / "scripts" / "workflow.py"


def main():
    if not DB.exists():
        print("no state db yet, skip")
        return

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        "SELECT id,current_stage,status FROM runs WHERE status='running' ORDER BY updated_at ASC"
    ).fetchall()

    if not rows:
        print("no running runs")
        return

    ok = 0
    failed = 0
    for r in rows:
        run_id = r["id"]
        p = subprocess.run([sys.executable, str(WORKFLOW), "tick", "--run-id", run_id], text=True, capture_output=True)
        if p.returncode == 0:
            ok += 1
            print(f"[OK] {run_id}\n{p.stdout.strip()}")
        else:
            failed += 1
            print(f"[FAIL] {run_id}\n{p.stdout.strip()}\n{p.stderr.strip()}")

    print(f"tick summary: ok={ok} failed={failed} total={len(rows)}")
    sys.exit(1 if failed else 0)


if __name__ == "__main__":
    main()
