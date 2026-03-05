#!/usr/bin/env python3
import json
import pathlib
import sqlite3
import sys

ROOT = pathlib.Path(__file__).resolve().parents[1]
COOP = ROOT / ".coop"
DB = COOP / "state.db"
CURSOR = COOP / "notify.cursor"

WATCH_EVENTS = {
    "gate.waiting",
    "gate.approved",
    "ack.waiting",
    "ack.received",
    "run.failed",
    "run.completed",
    "stage.changed",
}


def load_cursor():
    if not CURSOR.exists():
        return 0
    try:
        return int(CURSOR.read_text(encoding="utf-8").strip() or "0")
    except Exception:
        return 0


def save_cursor(v: int):
    COOP.mkdir(exist_ok=True)
    CURSOR.write_text(str(v), encoding="utf-8")


def main():
    if not DB.exists():
        print("no state db yet")
        return

    conn = sqlite3.connect(DB)
    conn.row_factory = sqlite3.Row
    last_id = load_cursor()
    rows = conn.execute(
        "SELECT id,run_id,event_type,payload_json,created_at FROM events WHERE id>? ORDER BY id ASC",
        (last_id,),
    ).fetchall()

    max_id = last_id
    for r in rows:
        max_id = max(max_id, r["id"])
        if r["event_type"] not in WATCH_EVENTS:
            continue
        payload = {}
        if r["payload_json"]:
            try:
                payload = json.loads(r["payload_json"])
            except Exception:
                payload = {"raw": r["payload_json"]}
        print(json.dumps({
            "event_id": r["id"],
            "run_id": r["run_id"],
            "event": r["event_type"],
            "created_at": r["created_at"],
            "payload": payload,
        }, ensure_ascii=False))

    save_cursor(max_id)


if __name__ == "__main__":
    main()
