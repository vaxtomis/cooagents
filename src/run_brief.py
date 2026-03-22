"""Lightweight run summary builder for OpenClaw context queries."""
from __future__ import annotations

from datetime import datetime, timezone

STAGE_META = {
    "INIT":               ("初始化", "automatic"),
    "REQ_COLLECTING":     ("等待需求提交", "manual"),
    "REQ_REVIEW":         ("需求审批中", "gate"),
    "DESIGN_QUEUED":      ("设计任务排队中", "automatic"),
    "DESIGN_DISPATCHED":  ("设计 Agent 启动中", "automatic"),
    "DESIGN_RUNNING":     ("设计 Agent 执行中", "automatic"),
    "DESIGN_REVIEW":      ("设计审批中", "gate"),
    "DEV_QUEUED":         ("开发任务排队中", "automatic"),
    "DEV_DISPATCHED":     ("开发 Agent 启动中", "automatic"),
    "DEV_RUNNING":        ("开发 Agent 执行中", "automatic"),
    "DEV_REVIEW":         ("开发审批中", "gate"),
    "MERGE_QUEUED":       ("合并排队中", "automatic"),
    "MERGING":            ("合并执行中", "automatic"),
    "MERGED":             ("已合并完成", "terminal"),
    "MERGE_CONFLICT":     ("合并冲突待解决", "manual"),
    "FAILED":             ("执行失败", "terminal"),
}

_STAGE_TO_GATE = {
    "REQ_REVIEW": "req",
    "DESIGN_REVIEW": "design",
    "DEV_REVIEW": "dev",
}

ALL_GATES = ["req", "design", "dev"]

# Stages that represent meaningful decision/action points (not automatic hops)
_MEANINGFUL_STAGES = set(_STAGE_TO_GATE.keys()) | {"MERGE_CONFLICT", "REQ_COLLECTING", "FAILED"}


def _elapsed_sec(iso_ts: str | None) -> int | None:
    if not iso_ts:
        return None
    try:
        t = datetime.fromisoformat(iso_ts)
        if t.tzinfo is None:
            t = t.replace(tzinfo=timezone.utc)
        return max(0, int((datetime.now(timezone.utc) - t).total_seconds()))
    except Exception:
        return None


def _stage_summary(stage: str, job: dict | None, max_turns: int | None) -> str:
    desc = STAGE_META.get(stage, (stage, "unknown"))[0]
    if not job:
        return desc

    agent = job.get("agent_type", "agent")
    host = job.get("host", job.get("host_id", "unknown"))
    status = job.get("job_status", job.get("status", "unknown"))
    turns = job.get("turn_count", 0)

    if status == "running" and max_turns:
        return f"{agent} 正在 {host} 上执行，已完成 {turns}/{max_turns} 轮"
    if status == "running":
        return f"{agent} 正在 {host} 上执行，当前第 {turns} 轮"
    if status == "starting":
        return f"{agent} 正在 {host} 上启动"
    return desc


async def build_brief(db, run_id: str) -> dict | None:
    run = await db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
    if not run:
        return None

    run = dict(run)
    current_stage = run["current_stage"]
    stage_desc, stage_type = STAGE_META.get(current_stage, (current_stage, "unknown"))

    # --- current job info ---
    active_job = await db.fetchone(
        "SELECT j.*, h.host FROM jobs j LEFT JOIN agent_hosts h ON j.host_id = h.id "
        "WHERE j.run_id=? AND j.status IN ('starting','running') "
        "ORDER BY j.started_at DESC LIMIT 1",
        (run_id,),
    )

    job_info = None
    if active_job:
        active_job = dict(active_job)
        job_info = {
            "job_id": active_job["id"],
            "job_status": active_job["status"],
            "agent_type": active_job.get("agent_type"),
            "turn_count": active_job.get("turn_count", 0),
            "host": active_job.get("host", active_job.get("host_id")),
            "elapsed_sec": _elapsed_sec(active_job.get("running_started_at") or active_job.get("started_at")),
        }

    max_turns = None
    if current_stage in ("DESIGN_RUNNING", "DESIGN_DISPATCHED"):
        max_turns = 3
    elif current_stage in ("DEV_RUNNING", "DEV_DISPATCHED"):
        max_turns = 5

    current = {
        "stage": current_stage,
        "description": stage_desc,
        "action_type": stage_type,
        "since": run.get("updated_at"),
        "elapsed_sec": _elapsed_sec(run.get("updated_at")),
        "summary": _stage_summary(current_stage, job_info, max_turns),
    }
    if job_info:
        current.update(job_info)

    # --- previous step ---
    steps = await db.fetchall(
        "SELECT * FROM steps WHERE run_id=? ORDER BY created_at DESC",
        (run_id,),
    )

    previous = None
    if steps:
        target_step = None
        for s in steps:
            if s["from_stage"] in _MEANINGFUL_STAGES:
                target_step = dict(s)
                break
        if not target_step:
            target_step = dict(steps[0])

        prev_stage = target_step["from_stage"]
        prev_gate = _STAGE_TO_GATE.get(prev_stage)

        result = None
        reason = None
        by = None

        if prev_gate:
            approval = await db.fetchone(
                "SELECT * FROM approvals WHERE run_id=? AND gate=? ORDER BY created_at DESC LIMIT 1",
                (run_id, prev_gate),
            )
            if approval:
                approval = dict(approval)
                result = approval["decision"]
                reason = approval.get("comment")
                by = approval.get("by")

        previous = {
            "stage": prev_stage,
            "result": result,
            "reason": reason,
            "by": by,
            "at": target_step["created_at"],
            "triggered_by": target_step.get("triggered_by"),
        }

    # --- progress ---
    all_approvals = await db.fetchall(
        "SELECT gate, decision FROM approvals WHERE run_id=? ORDER BY created_at",
        (run_id,),
    )
    latest_decision = {}
    for a in all_approvals:
        latest_decision[a["gate"]] = a["decision"]
    gates_passed = [g for g in ALL_GATES if latest_decision.get(g) == "approved"]

    gates_remaining = [g for g in ALL_GATES if g not in gates_passed]

    artifact_count = await db.fetchone(
        "SELECT COUNT(*) as c FROM artifacts WHERE run_id=?", (run_id,)
    )

    progress = {
        "gates_passed": gates_passed,
        "gates_remaining": gates_remaining,
        "artifacts_count": artifact_count["c"] if artifact_count else 0,
    }

    return {
        "run_id": run_id,
        "ticket": run["ticket"],
        "status": run["status"],
        "created_at": run["created_at"],
        "current": current,
        "previous": previous,
        "progress": progress,
    }


async def resolve_run_by_ticket(db, ticket: str) -> str | None:
    """Return the run_id of the most recent active run for a ticket.

    Priority: running > failed > completed > cancelled.
    Among same-status runs, pick the most recently updated.
    """
    row = await db.fetchone(
        "SELECT id FROM runs WHERE ticket=? "
        "ORDER BY CASE status "
        "  WHEN 'running' THEN 0 "
        "  WHEN 'failed' THEN 1 "
        "  WHEN 'completed' THEN 2 "
        "  WHEN 'cancelled' THEN 3 "
        "END, updated_at DESC LIMIT 1",
        (ticket,),
    )
    return row["id"] if row else None
