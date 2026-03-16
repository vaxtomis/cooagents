import hashlib
from pathlib import Path
from datetime import datetime, timezone


class ArtifactManager:
    def __init__(self, db):
        self.db = db

    async def register(self, run_id, kind, path, stage, git_ref=None) -> int:
        """Register artifact. Computes content_hash and byte_size. Returns artifact id.
        Auto-increments version if same run_id+kind has existing versions."""
        content_hash = self._compute_hash(path)
        byte_size = Path(path).stat().st_size

        # Get max version for this run_id + kind
        row = await self.db.fetchone(
            "SELECT MAX(version) as max_v FROM artifacts WHERE run_id=? AND kind=?",
            (run_id, kind),
        )
        version = 1 if row is None or row["max_v"] is None else row["max_v"] + 1

        now = datetime.now(timezone.utc).isoformat()
        aid = await self.db.execute(
            """INSERT INTO artifacts(run_id, kind, path, version, status, content_hash, byte_size, stage, git_ref, created_at)
               VALUES(?,?,?,?,?,?,?,?,?,?)""",
            (run_id, kind, path, version, "draft", content_hash, byte_size, stage, git_ref, now),
        )
        return aid

    async def scan_and_register(self, run_id, ticket, stage, worktree, base_commit=None) -> list[dict]:
        """Scan worktree for artifacts based on stage.
        Design stage: DES-{ticket}*.md, ADR-{ticket}*.md
        Dev stage: TEST-REPORT-{ticket}*.md, code changes via git log
        Only registers new/changed files (content_hash check)."""
        import glob

        registered = []
        patterns = []

        if "DESIGN" in stage:
            patterns = [
                ("design", f"docs/design/DES-{ticket}*.md"),
                ("adr", f"docs/design/ADR-{ticket}*.md"),
            ]
        elif "DEV" in stage:
            patterns = [
                ("test-report", f"docs/dev/TEST-REPORT-{ticket}*.md"),
            ]

        for kind, pattern in patterns:
            for filepath in glob.glob(str(Path(worktree) / pattern)):
                content_hash = self._compute_hash(filepath)
                # Check if already registered with same hash
                existing = await self.db.fetchone(
                    "SELECT id FROM artifacts WHERE run_id=? AND kind=? AND content_hash=?",
                    (run_id, kind, content_hash),
                )
                if existing is None:
                    aid = await self.register(run_id, kind, filepath, stage)
                    art = await self.db.fetchone("SELECT * FROM artifacts WHERE id=?", (aid,))
                    registered.append(dict(art))

        return registered

    async def get_by_run(self, run_id, kind=None, status=None) -> list[dict]:
        """List artifacts for a run with optional filters."""
        sql = "SELECT * FROM artifacts WHERE run_id=?"
        params = [run_id]
        if kind:
            sql += " AND kind=?"
            params.append(kind)
        if status:
            sql += " AND status=?"
            params.append(status)
        sql += " ORDER BY created_at"
        rows = await self.db.fetchall(sql, tuple(params))
        return [dict(r) for r in rows]

    async def get_content(self, artifact_id) -> str:
        """Read and return file content for an artifact."""
        row = await self.db.fetchone("SELECT path FROM artifacts WHERE id=?", (artifact_id,))
        if row is None:
            raise ValueError(f"Artifact {artifact_id} not found")
        return Path(row["path"]).read_text(encoding="utf-8")

    async def get_diff(self, artifact_id) -> str | None:
        """Diff against previous version of same run_id+kind. Returns None if v1."""
        row = await self.db.fetchone("SELECT * FROM artifacts WHERE id=?", (artifact_id,))
        if row is None or row["version"] <= 1:
            return None
        prev = await self.db.fetchone(
            "SELECT path FROM artifacts WHERE run_id=? AND kind=? AND version=?",
            (row["run_id"], row["kind"], row["version"] - 1),
        )
        if prev is None:
            return None
        # Simple line diff
        import difflib

        old = Path(prev["path"]).read_text(encoding="utf-8").splitlines()
        new = Path(row["path"]).read_text(encoding="utf-8").splitlines()
        diff = difflib.unified_diff(old, new, lineterm="")
        return "\n".join(diff)

    async def update_status(self, artifact_id, status, review_comment=None):
        """Update artifact status (submitted/approved/rejected)."""
        sql = "UPDATE artifacts SET status=?"
        params = [status]
        if review_comment:
            sql += ", review_comment=?"
            params.append(review_comment)
        sql += " WHERE id=?"
        params.append(artifact_id)
        await self.db.execute(sql, tuple(params))

    async def submit_all(self, run_id, stage):
        """Mark all draft artifacts for this run+stage as submitted."""
        await self.db.execute(
            "UPDATE artifacts SET status='submitted' WHERE run_id=? AND stage=? AND status='draft'",
            (run_id, stage),
        )

    def _compute_hash(self, filepath) -> str:
        """SHA256 of file content."""
        h = hashlib.sha256()
        with open(filepath, "rb") as f:
            for chunk in iter(lambda: f.read(8192), b""):
                h.update(chunk)
        return h.hexdigest()

    async def render_task(self, template_path, variables: dict, output_path) -> str:
        """Render a task template with {{variable}} substitution."""
        content = Path(template_path).read_text(encoding="utf-8")
        for key, value in variables.items():
            content = content.replace("{{" + key + "}}", str(value))
        Path(output_path).parent.mkdir(parents=True, exist_ok=True)
        Path(output_path).write_text(content, encoding="utf-8")
        return output_path
