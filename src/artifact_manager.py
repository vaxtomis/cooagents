import hashlib
from pathlib import Path
from datetime import datetime, timezone


class ArtifactManager:
    def __init__(self, db, project_root=None):
        self.db = db
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]

    def _resolve_project_path(self, path) -> Path:
        path = Path(path)
        if not path.is_absolute():
            path = self.project_root / path
        return path

    def _assert_path_within_project(self, path) -> Path:
        """Resolve path and assert it stays within project_root.

        Why: artifact rows persist filesystem paths; if a row is tampered with
        (or future code passes a user-supplied path), reading the content would
        expose arbitrary files (e.g. /etc/passwd).
        """
        resolved = Path(path).resolve()
        root = self.project_root.resolve()
        try:
            resolved.relative_to(root)
        except ValueError:
            raise ValueError(f"Artifact path escapes project root: {path}")
        return resolved

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
        safe_path = self._assert_path_within_project(row["path"])
        return safe_path.read_text(encoding="utf-8")

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

        old = self._assert_path_within_project(prev["path"]).read_text(encoding="utf-8").splitlines()
        new = self._assert_path_within_project(row["path"]).read_text(encoding="utf-8").splitlines()
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
        """Render a task template with Jinja2."""
        from jinja2 import Environment, FileSystemLoader, select_autoescape

        template_file = self._resolve_project_path(template_path)
        output_file = self._resolve_project_path(output_path)
        env = Environment(
            loader=FileSystemLoader(str(template_file.parent)),
            keep_trailing_newline=True,
            autoescape=select_autoescape(enabled_extensions=("html", "htm", "xml"), default_for_string=False),
        )
        template = env.get_template(template_file.name)
        content = template.render(**variables)
        output_file.parent.mkdir(parents=True, exist_ok=True)
        output_file.write_text(content, encoding="utf-8")
        return str(output_file)
