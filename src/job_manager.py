import uuid
from datetime import datetime, timezone
from pathlib import Path


class JobManager:
    def __init__(self, db, coop_dir=".coop", project_root=None):
        self.db = db
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
        self.coop_dir = self._resolve_path(coop_dir)

    def _resolve_path(self, path):
        path = Path(path)
        if not path.is_absolute():
            path = self.project_root / path
        return path

    async def create_job(self, run_id, host_id, agent_type, stage, task_file, worktree, base_commit, timeout_sec, session_name=None) -> str:
        job_id = f"job-{uuid.uuid4().hex[:12]}"
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,task_file,worktree,base_commit,session_name,started_at)
               VALUES(?,?,?,?,?,?,?,?,?,?,?)""",
            (job_id, run_id, host_id, agent_type, stage, "starting", task_file, worktree, base_commit, session_name, now)
        )
        return job_id

    async def update_status(self, job_id, status, ended_at=None, snapshot_json=None):
        sql = "UPDATE jobs SET status=?"
        params = [status]
        if ended_at:
            sql += ", ended_at=?"
            params.append(ended_at)
        if snapshot_json:
            sql += ", snapshot_json=?"
            params.append(snapshot_json)
        sql += " WHERE id=?"
        params.append(job_id)
        await self.db.execute(sql, tuple(params))

    async def get_active_job(self, run_id):
        return await self.db.fetchone(
            "SELECT * FROM jobs WHERE run_id=? AND status IN ('starting','running') ORDER BY started_at DESC LIMIT 1",
            (run_id,)
        )

    async def get_jobs(self, run_id):
        rows = await self.db.fetchall("SELECT * FROM jobs WHERE run_id=? ORDER BY started_at", (run_id,))
        return [dict(r) for r in rows]

    async def increment_turn(self, job_id) -> int:
        await self.db.execute(
            "UPDATE jobs SET turn_count = turn_count + 1 WHERE id=?", (job_id,)
        )
        job = await self.db.fetchone("SELECT turn_count FROM jobs WHERE id=?", (job_id,))
        return job["turn_count"]

    async def record_turn(self, job_id, turn_num, prompt_file, verdict, detail):
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            """INSERT INTO turns(job_id, turn_num, prompt_file, verdict, detail, started_at)
               VALUES(?,?,?,?,?,?)""",
            (job_id, turn_num, prompt_file, verdict, detail, now)
        )

    async def get_turns(self, job_id) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM turns WHERE job_id=? ORDER BY turn_num", (job_id,)
        )
        return [dict(r) for r in rows]

    async def get_output(self, job_id):
        events_path = self.coop_dir / "jobs" / job_id / "events.jsonl"
        if events_path.exists():
            return events_path.read_text(encoding="utf-8")
        log_path = self.coop_dir / "jobs" / job_id / "stdout.log"
        if log_path.exists():
            return log_path.read_text(encoding="utf-8")
        return ""
