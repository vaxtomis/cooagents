#!/usr/bin/env python3
import argparse
import datetime as dt
import fcntl
import json
import os
import pathlib
import sqlite3
import subprocess
import sys
import uuid

ROOT = pathlib.Path(__file__).resolve().parents[1]
COOP_DIR = ROOT / ".coop"
RUNS_DIR = COOP_DIR / "runs"
DB_PATH = COOP_DIR / "state.db"
LOCK_PATH = COOP_DIR / "workflow.lock"
SCHEMA_PATH = ROOT / "db" / "schema.sql"


def now_iso():
    return dt.datetime.utcnow().replace(microsecond=0).isoformat() + "Z"


def run_cmd(cmd, cwd=None, check=True):
    p = subprocess.run(cmd, cwd=cwd, text=True, capture_output=True)
    if check and p.returncode != 0:
        raise RuntimeError(f"cmd failed: {' '.join(cmd)}\n{p.stdout}\n{p.stderr}")
    return p


def ensure_paths():
    COOP_DIR.mkdir(exist_ok=True)
    RUNS_DIR.mkdir(exist_ok=True)


def db_conn():
    ensure_paths()
    conn = sqlite3.connect(DB_PATH)
    conn.row_factory = sqlite3.Row
    with open(SCHEMA_PATH, "r", encoding="utf-8") as f:
        conn.executescript(f.read())
    return conn


def event(conn, run_id, event_type, payload=None):
    conn.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
        (run_id, event_type, json.dumps(payload or {}, ensure_ascii=False), now_iso()),
    )


def snapshot(conn, run_id):
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    steps = [dict(x) for x in conn.execute("SELECT * FROM steps WHERE run_id=? ORDER BY id", (run_id,)).fetchall()]
    approvals = [dict(x) for x in conn.execute("SELECT gate,approved_by,comment,approved_at FROM approvals WHERE run_id=? ORDER BY id", (run_id,)).fetchall()]
    events = [dict(x) for x in conn.execute("SELECT id,event_type,payload_json,created_at FROM events WHERE run_id=? ORDER BY id DESC LIMIT 30", (run_id,)).fetchall()]
    obj = {"run": dict(run), "steps": steps, "approvals": approvals, "recent_events": events}
    run_dir = RUNS_DIR / run_id
    run_dir.mkdir(parents=True, exist_ok=True)
    (run_dir / "state.json").write_text(json.dumps(obj, ensure_ascii=False, indent=2), encoding="utf-8")


def update_run(conn, run_id, status=None, stage=None):
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise RuntimeError(f"run not found: {run_id}")
    new_status = status or run["status"]
    new_stage = stage or run["current_stage"]
    conn.execute(
        "UPDATE runs SET status=?, current_stage=?, updated_at=? WHERE id=?",
        (new_status, new_stage, now_iso(), run_id),
    )


def step_open(conn, run_id, stage, assignee, note=""):
    conn.execute(
        "INSERT INTO steps(run_id,stage,assignee,status,started_at,note) VALUES(?,?,?,?,?,?)",
        (run_id, stage, assignee, "running", now_iso(), note),
    )


def step_done(conn, run_id, stage, note=""):
    conn.execute(
        "UPDATE steps SET status='done', ended_at=?, note=COALESCE(note,'') || ? WHERE id=(SELECT id FROM steps WHERE run_id=? AND stage=? AND status='running' ORDER BY id DESC LIMIT 1)",
        (now_iso(), ("\n" + note) if note else "", run_id, stage),
    )


def has_approval(conn, run_id, gate):
    return conn.execute("SELECT 1 FROM approvals WHERE run_id=? AND gate=?", (run_id, gate)).fetchone() is not None


def ensure_worktree(repo, ticket, phase):
    if phase == "design":
        branch = f"feat/{ticket}-design"
        wt = pathlib.Path(repo).parent / f"wt-{ticket}-design"
    else:
        branch = f"feat/{ticket}-dev"
        wt = pathlib.Path(repo).parent / f"wt-{ticket}-dev"

    run_cmd(["git", "fetch", "origin"], cwd=repo, check=False)
    branch_exists = run_cmd(["git", "show-ref", "--verify", f"refs/heads/{branch}"], cwd=repo, check=False).returncode == 0

    if wt.exists() and (wt / ".git").exists():
        return branch, str(wt)

    if branch_exists:
        run_cmd(["git", "worktree", "add", str(wt), branch], cwd=repo)
    else:
        run_cmd(["git", "worktree", "add", "-b", branch, str(wt)], cwd=repo)
    return branch, str(wt)


def ensure_tmux(session, workdir, command):
    exists = run_cmd(["tmux", "has-session", "-t", session], check=False)
    if exists.returncode != 0:
        run_cmd(["tmux", "new-session", "-d", "-s", session, "-c", workdir])
        run_cmd(["tmux", "send-keys", "-t", session, command, "Enter"])


def stage_tick(conn, run_id):
    run = conn.execute("SELECT * FROM runs WHERE id=?", (run_id,)).fetchone()
    if not run:
        raise RuntimeError(f"run not found: {run_id}")

    ticket = run["ticket"]
    repo = run["repo_path"]
    stage = run["current_stage"]

    req_file = pathlib.Path(repo) / "docs" / "req" / f"REQ-{ticket}.md"

    if stage == "INIT":
        step_open(conn, run_id, "REQ_COLLECTING", "openclaw", "开始收集需求")
        update_run(conn, run_id, status="running", stage="REQ_COLLECTING")
        event(conn, run_id, "stage.changed", {"to": "REQ_COLLECTING"})

    elif stage == "REQ_COLLECTING":
        if req_file.exists():
            step_done(conn, run_id, "REQ_COLLECTING", "需求文档已生成")
            conn.execute(
                "INSERT INTO artifacts(run_id,kind,path,created_at) VALUES(?,?,?,?)",
                (run_id, "req", str(req_file.relative_to(repo)), now_iso()),
            )
            update_run(conn, run_id, stage="REQ_READY")
            event(conn, run_id, "gate.waiting", {"gate": "req"})

    elif stage == "REQ_READY":
        if has_approval(conn, run_id, "req"):
            branch, wt = ensure_worktree(repo, ticket, "design")
            ensure_tmux(f"design-{ticket}", wt, "claude")
            step_open(conn, run_id, "DESIGN_RUNNING", "claude", f"worktree={wt} branch={branch}")
            update_run(conn, run_id, stage="DESIGN_RUNNING")
            event(conn, run_id, "stage.changed", {"to": "DESIGN_RUNNING", "worktree": wt, "branch": branch})

    elif stage == "DESIGN_RUNNING":
        des_file = pathlib.Path(repo).parent / f"wt-{ticket}-design" / "docs" / "design" / f"DES-{ticket}.md"
        if des_file.exists():
            step_done(conn, run_id, "DESIGN_RUNNING", "设计文档已生成")
            conn.execute(
                "INSERT INTO artifacts(run_id,kind,path,created_at) VALUES(?,?,?,?)",
                (run_id, "design", str(des_file), now_iso()),
            )
            update_run(conn, run_id, stage="DESIGN_DONE")
            event(conn, run_id, "gate.waiting", {"gate": "design"})

    elif stage == "DESIGN_DONE":
        if has_approval(conn, run_id, "design"):
            branch, wt = ensure_worktree(repo, ticket, "dev")
            ensure_tmux(f"dev-{ticket}", wt, "codex")
            step_open(conn, run_id, "DEV_RUNNING", "codex", f"worktree={wt} branch={branch}")
            update_run(conn, run_id, stage="DEV_RUNNING")
            event(conn, run_id, "stage.changed", {"to": "DEV_RUNNING", "worktree": wt, "branch": branch})

    elif stage == "DEV_RUNNING":
        test_file = pathlib.Path(repo).parent / f"wt-{ticket}-dev" / "docs" / "dev" / f"TEST-REPORT-{ticket}.md"
        if test_file.exists():
            step_done(conn, run_id, "DEV_RUNNING", "开发与测试产物已生成")
            conn.execute(
                "INSERT INTO artifacts(run_id,kind,path,created_at) VALUES(?,?,?,?)",
                (run_id, "test", str(test_file), now_iso()),
            )
            update_run(conn, run_id, status="completed", stage="COMPLETED")
            event(conn, run_id, "run.completed", {"run_id": run_id})


def cmd_start(args):
    conn = db_conn()
    run_id = args.run_id or f"run-{dt.datetime.utcnow().strftime('%Y%m%d-%H%M%S')}-{uuid.uuid4().hex[:6]}"
    repo = str(pathlib.Path(args.repo).resolve())
    conn.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        (run_id, args.ticket, repo, "running", "INIT", now_iso(), now_iso()),
    )
    event(conn, run_id, "run.created", {"ticket": args.ticket, "repo": repo})
    conn.commit()
    snapshot(conn, run_id)
    print(run_id)


def cmd_tick(args):
    with open(LOCK_PATH, "a+") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        conn = db_conn()
        try:
            stage_tick(conn, args.run_id)
            conn.commit()
        except Exception as e:
            conn.rollback()
            update_run(conn, args.run_id, status="failed")
            event(conn, args.run_id, "run.failed", {"error": str(e)})
            conn.commit()
            snapshot(conn, args.run_id)
            print(json.dumps({"run_id": args.run_id, "status": "failed", "error": str(e)}, ensure_ascii=False, indent=2))
            sys.exit(1)

        snapshot(conn, args.run_id)
        run = conn.execute("SELECT id,status,current_stage,updated_at FROM runs WHERE id=?", (args.run_id,)).fetchone()
        print(json.dumps(dict(run), ensure_ascii=False, indent=2))


def cmd_retry(args):
    conn = db_conn()
    run = conn.execute("SELECT id,status,current_stage FROM runs WHERE id=?", (args.run_id,)).fetchone()
    if not run:
        print("run not found", file=sys.stderr)
        sys.exit(1)
    if run["status"] != "failed":
        print(f"run {args.run_id} is not failed (status={run['status']})")
        return
    update_run(conn, args.run_id, status="running")
    event(conn, args.run_id, "run.retried", {"by": args.by, "note": args.note or ""})
    conn.commit()
    snapshot(conn, args.run_id)
    print(f"retried {args.run_id}")


def cmd_approve(args):
    conn = db_conn()
    conn.execute(
        "INSERT OR REPLACE INTO approvals(run_id,gate,approved_by,comment,approved_at) VALUES(?,?,?,?,?)",
        (args.run_id, args.gate, args.by, args.comment or "", now_iso()),
    )
    event(conn, args.run_id, "gate.approved", {"gate": args.gate, "by": args.by, "comment": args.comment or ""})
    conn.commit()
    snapshot(conn, args.run_id)
    print(f"approved {args.gate} for {args.run_id}")


def cmd_status(args):
    conn = db_conn()
    run = conn.execute("SELECT * FROM runs WHERE id=?", (args.run_id,)).fetchone()
    if not run:
        print("run not found", file=sys.stderr)
        sys.exit(1)
    print(json.dumps(dict(run), ensure_ascii=False, indent=2))
    print("-- approvals --")
    for x in conn.execute("SELECT gate,approved_by,approved_at,comment FROM approvals WHERE run_id=? ORDER BY id", (args.run_id,)):
        print(dict(x))
    print("-- recent events --")
    for x in conn.execute("SELECT event_type,created_at,payload_json FROM events WHERE run_id=? ORDER BY id DESC LIMIT 10", (args.run_id,)):
        print(dict(x))


def cmd_list(args):
    conn = db_conn()
    rows = conn.execute("SELECT id,ticket,status,current_stage,updated_at FROM runs ORDER BY updated_at DESC LIMIT ?", (args.limit,)).fetchall()
    for r in rows:
        print(json.dumps(dict(r), ensure_ascii=False))


def main():
    parser = argparse.ArgumentParser(description="event-driven workflow orchestrator")
    sub = parser.add_subparsers(dest="cmd", required=True)

    p = sub.add_parser("start")
    p.add_argument("--ticket", required=True)
    p.add_argument("--repo", default=str(ROOT))
    p.add_argument("--run-id")
    p.set_defaults(func=cmd_start)

    p = sub.add_parser("tick")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_tick)

    p = sub.add_parser("approve")
    p.add_argument("--run-id", required=True)
    p.add_argument("--gate", choices=["req", "design"], required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--comment")
    p.set_defaults(func=cmd_approve)

    p = sub.add_parser("status")
    p.add_argument("--run-id", required=True)
    p.set_defaults(func=cmd_status)

    p = sub.add_parser("retry")
    p.add_argument("--run-id", required=True)
    p.add_argument("--by", required=True)
    p.add_argument("--note")
    p.set_defaults(func=cmd_retry)

    p = sub.add_parser("list")
    p.add_argument("--limit", type=int, default=20)
    p.set_defaults(func=cmd_list)

    args = parser.parse_args()
    args.func(args)


if __name__ == "__main__":
    main()
