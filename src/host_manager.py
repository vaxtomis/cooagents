from datetime import datetime, timezone


class HostManager:
    def __init__(self, db):
        self.db = db

    async def _count_active_jobs(self, host_id):
        row = await self.db.fetchone(
            "SELECT COUNT(*) as cnt FROM jobs j "
            "WHERE j.host_id=? AND j.status IN ('starting','running') "
            "AND EXISTS (SELECT 1 FROM runs r WHERE r.id = j.run_id)",
            (host_id,)
        )
        return row["cnt"] if row else 0

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
            d["current_load"] = await self._count_active_jobs(d["id"])
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
            d["current_load"] = await self._count_active_jobs(d["id"])
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
        # Load is derived from real jobs in the jobs table.
        # Keep this method as a no-op for backward compatibility with callers.
        return None

    async def decrement_load(self, host_id):
        # Load is derived from real jobs in the jobs table.
        # Keep this method as a no-op for backward compatibility with callers.
        return None

    async def set_status(self, host_id, status):
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE agent_hosts SET status=?, updated_at=? WHERE id=?",
            (status, now, host_id)
        )

    def _check_local_cli(self, agent_type):
        """Verify the required CLI tools are available locally for the given agent_type."""
        import shutil
        if agent_type == "claude":
            return bool(shutil.which("claude"))
        elif agent_type == "codex":
            return bool(shutil.which("codex"))
        else:  # "both"
            return bool(shutil.which("claude")) and bool(shutil.which("codex"))

    async def health_check(self, host_id):
        host = await self.db.fetchone("SELECT * FROM agent_hosts WHERE id=?", (host_id,))
        if not host:
            return False
        if host["host"] == "local":
            cli_ok = self._check_local_cli(host["agent_type"])
            status = "active" if cli_ok else "offline"
        else:
            try:
                import asyncssh
                agent_type = host["agent_type"]
                async with asyncssh.connect(
                    host["host"],
                    # known_hosts omitted -> asyncssh uses ~/.ssh/known_hosts
                    client_keys=[host["ssh_key"]] if host.get("ssh_key") else None,
                ) as conn:
                    if agent_type == "claude":
                        cmds = ["claude"]
                    elif agent_type == "codex":
                        cmds = ["codex"]
                    else:
                        cmds = ["claude", "codex"]
                    cli_ok = True
                    for cmd in cmds:
                        result = await conn.run(f"which {cmd}")
                        if result.returncode != 0:
                            cli_ok = False
                            break
                    status = "active" if cli_ok else "offline"
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
