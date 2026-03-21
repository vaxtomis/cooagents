"""Diagnostic API endpoints for trace querying."""
from __future__ import annotations

import json
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse


def create_diagnostics_router(db=None):
    router = APIRouter(tags=["diagnostics"])

    def _get_db(request: Request = None):
        if db is not None:
            return db
        return request.app.state.db

    @router.get("/runs/{run_id}/trace")
    async def run_trace(
        request: Request,
        run_id: str,
        level: str = Query("info", pattern="^(debug|info|warning|error)$"),
        span_type: str | None = Query(None),
        limit: int = Query(200, le=1000),
        offset: int = Query(0, ge=0),
    ):
        d = _get_db(request)
        run = await d.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
        if not run:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": f"Run {run_id} not found"})

        level_order = {"debug": 0, "info": 1, "warning": 2, "error": 3}
        min_level = level_order.get(level, 1)

        # Build query
        conditions = ["run_id = ?"]
        params: list = [run_id]

        levels_included = [lv for lv, v in level_order.items() if v >= min_level]
        placeholders = ",".join("?" * len(levels_included))
        conditions.append(f"level IN ({placeholders})")
        params.extend(levels_included)

        if span_type:
            conditions.append("span_type = ?")
            params.append(span_type)

        where = " AND ".join(conditions)

        # Total count
        count_row = await d.fetchone(f"SELECT COUNT(*) as c FROM events WHERE {where}", tuple(params))
        total = count_row["c"] if count_row else 0

        # Fetch events
        params_with_pagination = list(params) + [limit, offset]
        events = await d.fetchall(
            f"SELECT * FROM events WHERE {where} ORDER BY created_at, id LIMIT ? OFFSET ?",
            tuple(params_with_pagination),
        )

        # Parse payload_json for response
        for evt in events:
            if evt.get("payload_json"):
                try:
                    evt["payload"] = json.loads(evt["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    evt["payload"] = evt["payload_json"]
            else:
                evt["payload"] = None
            evt.pop("payload_json", None)

        # Build summary
        jobs = await d.fetchall(
            "SELECT id, stage, status, started_at, ended_at FROM jobs WHERE run_id=? ORDER BY started_at",
            (run_id,),
        )
        steps = await d.fetchall(
            "SELECT to_stage FROM steps WHERE run_id=? ORDER BY created_at", (run_id,)
        )
        stages_visited = [s["to_stage"] for s in steps]

        error_count = await d.fetchone(
            "SELECT COUNT(*) as c FROM events WHERE run_id=? AND level='error'", (run_id,)
        )
        warn_count = await d.fetchone(
            "SELECT COUNT(*) as c FROM events WHERE run_id=? AND level='warning'", (run_id,)
        )

        job_summaries = []
        for j in jobs:
            duration = None
            if j.get("started_at") and j.get("ended_at"):
                from datetime import datetime
                try:
                    s = datetime.fromisoformat(j["started_at"])
                    e = datetime.fromisoformat(j["ended_at"])
                    duration = int((e - s).total_seconds() * 1000)
                except Exception:
                    pass
            job_summaries.append({
                "job_id": j["id"],
                "stage": j["stage"],
                "status": j["status"],
                "duration_ms": duration,
            })

        # Compute total_duration_ms from first to last event
        total_duration_ms = None
        if events:
            from datetime import datetime as dt
            try:
                first = dt.fromisoformat(events[0]["created_at"])
                last = dt.fromisoformat(events[-1]["created_at"])
                total_duration_ms = int((last - first).total_seconds() * 1000)
            except Exception:
                pass

        return {
            "run_id": run_id,
            "status": run["status"],
            "current_stage": run["current_stage"],
            "failed_at_stage": run.get("failed_at_stage"),
            "created_at": run["created_at"],
            "summary": {
                "total_events": total,
                "errors": error_count["c"] if error_count else 0,
                "warnings": warn_count["c"] if warn_count else 0,
                "stages_visited": stages_visited,
                "total_duration_ms": total_duration_ms,
                "jobs": job_summaries,
            },
            "events": events,
            "pagination": {"limit": limit, "offset": offset, "has_more": (offset + limit) < total},
        }

    @router.get("/jobs/{job_id}/diagnosis")
    async def job_diagnosis(request: Request, job_id: str, level: str = Query("info")):
        d = _get_db(request)
        job = await d.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": f"Job {job_id} not found"})

        job = dict(job)

        # Duration
        duration_ms = None
        if job.get("started_at") and job.get("ended_at"):
            from datetime import datetime
            try:
                s = datetime.fromisoformat(job["started_at"])
                e = datetime.fromisoformat(job["ended_at"])
                duration_ms = int((e - s).total_seconds() * 1000)
            except Exception:
                pass

        # Turn count + turns
        turns = await d.fetchall("SELECT * FROM turns WHERE job_id=? ORDER BY turn_num", (job_id,))
        turn_count = len(turns)

        # Error info from events
        error_event = await d.fetchone(
            "SELECT error_detail FROM events WHERE job_id=? AND level='error' ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        )
        error_detail = error_event["error_detail"] if error_event else None
        error_summary = error_detail.strip().split("\n")[-1] if error_detail else None

        # Last output excerpt from events_file
        last_output = None
        events_file = job.get("events_file")
        if events_file:
            try:
                import os
                if os.path.exists(events_file):
                    with open(events_file, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(0, 2)
                        size = f.tell()
                        read_start = max(0, size - 500)
                        f.seek(read_start)
                        last_output = f.read()
            except Exception:
                pass

        # Host status at failure
        host_status = None
        if job.get("host_id"):
            host = await d.fetchone("SELECT status FROM agent_hosts WHERE id=?", (job["host_id"],))
            host_status = host["status"] if host else None

        # Events for this job
        events = await d.fetchall(
            "SELECT * FROM events WHERE job_id=? ORDER BY created_at", (job_id,)
        )
        for evt in events:
            if evt.get("payload_json"):
                try:
                    evt["payload"] = json.loads(evt["payload_json"])
                except Exception:
                    evt["payload"] = evt["payload_json"]
            else:
                evt["payload"] = None
            evt.pop("payload_json", None)

        return {
            "job_id": job_id,
            "run_id": job.get("run_id"),
            "host_id": job.get("host_id"),
            "agent_type": job.get("agent_type"),
            "stage": job.get("stage"),
            "status": job.get("status"),
            "session_name": job.get("session_name"),
            "started_at": job.get("started_at"),
            "ended_at": job.get("ended_at"),
            "diagnosis": {
                "duration_ms": duration_ms,
                "turn_count": turn_count,
                "error_summary": error_summary,
                "error_detail": error_detail,
                "last_output_excerpt": last_output,
                "failure_context": {
                    "stage_at_failure": job.get("stage"),
                    "host_status_at_failure": host_status,
                    "retry_count": job.get("resume_count", 0),
                },
            },
            "events": events,
            "turns": [dict(t) for t in turns],
        }

    @router.get("/traces/{trace_id}")
    async def trace_lookup(request: Request, trace_id: str, level: str = Query("info")):
        d = _get_db(request)

        # Apply level filtering
        level_order = {"debug": 0, "info": 1, "warning": 2, "error": 3}
        min_level = level_order.get(level, 1)
        levels_included = [lv for lv, v in level_order.items() if v >= min_level]
        placeholders = ",".join("?" * len(levels_included))

        events = await d.fetchall(
            f"SELECT * FROM events WHERE trace_id=? AND level IN ({placeholders}) ORDER BY created_at, id",
            (trace_id, *levels_included),
        )
        if not events:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": f"Trace {trace_id} not found"})

        for evt in events:
            if evt.get("payload_json"):
                try:
                    evt["payload"] = json.loads(evt["payload_json"])
                except Exception:
                    evt["payload"] = evt["payload_json"]
            else:
                evt["payload"] = None
            evt.pop("payload_json", None)

        affected_runs = list({e["run_id"] for e in events if e.get("run_id")})
        affected_jobs = list({e["job_id"] for e in events if e.get("job_id")})
        error_count = sum(1 for e in events if e.get("level") == "error")

        first_seen = events[0]["created_at"] if events else None
        last_seen = events[-1]["created_at"] if events else None

        # Compute total_duration_ms
        total_duration_ms = None
        if first_seen and last_seen:
            from datetime import datetime as dt
            try:
                total_duration_ms = int((dt.fromisoformat(last_seen) - dt.fromisoformat(first_seen)).total_seconds() * 1000)
            except Exception:
                pass

        # Determine origin from trace_id prefix
        origin = "scheduler" if trace_id.startswith("sched-") else "external" if "-" in trace_id else "internal"

        return {
            "trace_id": trace_id,
            "origin": origin,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "total_duration_ms": total_duration_ms,
            "affected_runs": affected_runs,
            "affected_jobs": affected_jobs,
            "error_count": error_count,
            "events": events,
        }

    return router
