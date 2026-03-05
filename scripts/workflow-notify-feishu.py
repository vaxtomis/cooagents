#!/usr/bin/env python3
import json
import os
import pathlib
import sqlite3
import sys
import urllib.request

ROOT = pathlib.Path(__file__).resolve().parents[1]
COOP = ROOT / ".coop"
DB = COOP / "state.db"
CURSOR = COOP / "notify-feishu.cursor"

WATCH_EVENTS = {
    "stage.changed",
    "gate.waiting",
    "gate.approved",
    "run.failed",
    "run.completed",
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


def http_post_json(url: str, payload: dict):
    data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"}, method="POST")
    with urllib.request.urlopen(req, timeout=10) as resp:
        body = resp.read().decode("utf-8", errors="ignore")
        return resp.status, body


def format_text(event_row):
    event = event_row["event_type"]
    payload = {}
    if event_row["payload_json"]:
        try:
            payload = json.loads(event_row["payload_json"])
        except Exception:
            payload = {"raw": event_row["payload_json"]}

    run_id = event_row["run_id"]
    ts = event_row["created_at"]

    if event == "stage.changed":
        return f"[cooagents] run={run_id}\n事件: 阶段变更\nto={payload.get('to')}\n时间={ts}"
    if event == "gate.waiting":
        return f"[cooagents] run={run_id}\n事件: 等待审批\ngate={payload.get('gate')}\n时间={ts}"
    if event == "gate.approved":
        return f"[cooagents] run={run_id}\n事件: 审批通过\ngate={payload.get('gate')} by={payload.get('by')}\n时间={ts}"
    if event == "run.failed":
        return f"[cooagents] run={run_id}\n事件: 运行失败\nerror={payload.get('error')}\n时间={ts}"
    if event == "run.completed":
        return f"[cooagents] run={run_id}\n事件: 运行完成\n时间={ts}"
    return f"[cooagents] run={run_id} event={event} time={ts}"


def send_to_feishu_webhook(webhook_url: str, text: str):
    payload = {"msg_type": "text", "content": {"text": text}}
    status, body = http_post_json(webhook_url, payload)
    if status >= 300:
        raise RuntimeError(f"feishu webhook failed status={status} body={body}")


def main():
    webhook = os.getenv("FEISHU_WEBHOOK_URL", "").strip()
    if not webhook:
        print("FEISHU_WEBHOOK_URL is required", file=sys.stderr)
        sys.exit(2)

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
    sent = 0
    for r in rows:
        max_id = max(max_id, r["id"])
        if r["event_type"] not in WATCH_EVENTS:
            continue
        text = format_text(r)
        send_to_feishu_webhook(webhook, text)
        sent += 1

    save_cursor(max_id)
    print(f"sent={sent} cursor={max_id}")


if __name__ == "__main__":
    main()
