import json
from datetime import datetime, timezone


class MergeManager:
    def __init__(self, db, webhook_notifier=None):
        self.db = db
        self.webhooks = webhook_notifier

    async def enqueue(self, run_id, branch, priority=0):
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT OR IGNORE INTO merge_queue(run_id,branch,priority,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (run_id, branch, priority, "waiting", now, now)
        )

    async def list_queue(self):
        rows = await self.db.fetchall(
            "SELECT * FROM merge_queue WHERE status IN ('waiting','merging','conflict') ORDER BY priority DESC, created_at ASC"
        )
        return [dict(r) for r in rows]

    async def get_status(self, run_id):
        row = await self.db.fetchone("SELECT status FROM merge_queue WHERE run_id=?", (run_id,))
        return row["status"] if row else None

    async def process_next(self):
        # Check if anything is already merging
        merging = await self.db.fetchone("SELECT * FROM merge_queue WHERE status='merging'")
        if merging:
            return None  # One at a time

        # Get next waiting item (priority DESC, created_at ASC)
        item = await self.db.fetchone(
            "SELECT * FROM merge_queue WHERE status='waiting' ORDER BY priority DESC, created_at ASC LIMIT 1"
        )
        if not item:
            return None

        item = dict(item)
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE merge_queue SET status='merging', updated_at=? WHERE id=?",
            (now, item["id"])
        )

        # Get the run to find repo_path
        run = await self.db.fetchone("SELECT * FROM runs WHERE id=?", (item["run_id"],))
        if not run:
            return None

        from src.git_utils import check_conflicts, rebase_on_main, merge_to_main

        # Try rebase first
        dev_worktree = run.get("dev_worktree")
        if dev_worktree:
            conflicts = await check_conflicts(dev_worktree)
            if conflicts:
                await self.db.execute(
                    "UPDATE merge_queue SET status='conflict', conflict_files_json=?, updated_at=? WHERE id=?",
                    (json.dumps(conflicts), now, item["id"])
                )
                if self.webhooks:
                    await self.webhooks.notify("merge.conflict", {
                        "run_id": item["run_id"],
                        "conflicts": conflicts
                    })
                return {"status": "conflict", "conflicts": conflicts}

            rebased = await rebase_on_main(dev_worktree)
            if not rebased:
                await self.db.execute(
                    "UPDATE merge_queue SET status='conflict', updated_at=? WHERE id=?",
                    (now, item["id"])
                )
                return {"status": "conflict"}

        # Merge
        success, result = await merge_to_main(dict(run)["repo_path"], item["branch"])
        if success:
            await self.db.execute(
                "UPDATE merge_queue SET status='merged', updated_at=? WHERE id=?",
                (now, item["id"])
            )
            if self.webhooks:
                await self.webhooks.notify("merge.completed", {
                    "run_id": item["run_id"],
                    "merge_commit": result
                })
            return {"status": "merged", "commit": result}
        else:
            await self.db.execute(
                "UPDATE merge_queue SET status='conflict', updated_at=? WHERE id=?",
                (now, item["id"])
            )
            return {"status": "conflict", "error": result}

    async def skip(self, run_id):
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "UPDATE merge_queue SET status='skipped', updated_at=? WHERE run_id=?",
            (now, run_id)
        )

    async def remove(self, run_id):
        await self.db.execute("DELETE FROM merge_queue WHERE run_id=?", (run_id,))
