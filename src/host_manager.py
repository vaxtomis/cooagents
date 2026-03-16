from datetime import datetime, timezone


class HostManager:
    def __init__(self, db):
        self.db = db

    async def register(self, id, host, agent_type, max_concurrent=2, ssh_key=None, labels=None):
        now = datetime.now(timezone.utc).isoformat()
        labels_json = None
        if labels:
            import json
            labels_json = json.dumps(labels)
        # Upsert
        existing = await self.db.fetchone("SELECT id FROM agent_hosts WHERE id=?", (id,))
        if existing:
            await self.db.execute(
                "UPDATE agent_hosts SET host=?, agent_type=?, max_concurrent=?, ssh_key=?, labels_json=?, updated_at=? WHERE id=?",
                (host, agent_type, max_concurrent, ssh_key, labels_json, now, id)
            )
        else:
            await self.db.execute(
                "INSERT INTO agent_hosts(id,host,agent_type,max_concurrent,ssh_key,labels_json,status,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
                (id, host, agent_type, max_concurrent, ssh_key, labels_json, "active", now, now)
            )

    async def remove(self, host_id):
        await self.db.execute("DELETE FROM agent_hosts WHERE id=?", (host_id,))

    async def list_all(self):
        rows = await self.db.fetchall("SELECT * FROM agent_hosts ORDER BY id")
        result = []
        for r in rows:
            d = dict(r)
            # Add current_load from jobs count
            load = await self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM jobs WHERE host_id=? AND status IN ('starting','running')",
                (d["id"],)
            )
            d["current_load"] = load["cnt"] if load else 0
            result.append(d)
        return result

    async def select_host(self, agent_type, preferred_host=None):
        # Get all active hosts matching agent_type
        rows = await self.db.fetchall(
            "SELECT * FROM agent_hosts WHERE status='active' AND (agent_type=? OR agent_type='both') ORDER BY id",
            (agent_type,)
        )
        candidates = []
        for r in rows:
            d = dict(r)
            load = await self.db.fetchone(
                "SELECT COUNT(*) as cnt FROM jobs WHERE host_id=? AND status IN ('starting','running')",
                (d["id"],)
            )
            d["current_load"] = load["cnt"] if load else 0
            if d["current_load"] < d["max_concurrent"]:
                candidates.append(d)

        if not candidates:
            return None

        # Check preference
        if preferred_host:
            for c in candidates:
                if c["id"] == preferred_host:
                    return c

        # Least loaded
        candidates.sort(key=lambda x: x["current_load"])
        return candidates[0]

    async def increment_load(self, host_id):
        # Track load by inserting a placeholder running job.
        # SQLite FK enforcement is off by default so run_id does not need a
        # real entry in the runs table.
        now = datetime.now(timezone.utc).isoformat()
        import uuid
        await self.db.execute(
            "INSERT INTO jobs(id,run_id,host_id,agent_type,stage,status,started_at) VALUES(?,?,?,?,?,?,?)",
            (f"load-{uuid.uuid4().hex[:8]}", f"load-{uuid.uuid4().hex[:8]}", host_id, "claude", "LOAD_TEST", "running", now)
        )

    async def decrement_load(self, host_id):
        # Mark one running placeholder job as completed
        job = await self.db.fetchone(
            "SELECT id FROM jobs WHERE host_id=? AND status='running' LIMIT 1",
            (host_id,)
        )
        if job:
            await self.db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job["id"],))

    async def set_status(self, host_id, status):
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE agent_hosts SET status=?, updated_at=? WHERE id=?",
            (status, now, host_id)
        )

    async def health_check(self, host_id):
        host = await self.db.fetchone("SELECT * FROM agent_hosts WHERE id=?", (host_id,))
        if not host:
            return False
        if host["host"] == "local":
            import shutil
            has_agent = shutil.which("claude") or shutil.which("codex")
            status = "active" if has_agent else "offline"
        else:
            try:
                import asyncssh
                async with asyncssh.connect(
                    host["host"],
                    known_hosts=None,
                    client_keys=[host["ssh_key"]] if host.get("ssh_key") else None,
                ):
                    pass
                status = "active"
            except Exception:
                status = "offline"
        await self.set_status(host_id, status)
        return status == "active"

    async def load_from_config(self, hosts_config):
        for h in hosts_config:
            await self.register(
                h["id"], h["host"], h["agent_type"],
                max_concurrent=h.get("max_concurrent", 2),
                ssh_key=h.get("ssh_key"),
                labels=h.get("labels")
            )
