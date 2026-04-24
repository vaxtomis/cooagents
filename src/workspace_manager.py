"""Workspace lifecycle — DB records + filesystem scaffolding + reconcile.

Phase 2 extends the Phase 1 skeleton with:
  * ``create_with_scaffold(title, slug)`` — writes disk + DB atomically
  * ``archive_with_scaffold(workspace_id)`` — DB status + workspace.md front-matter
  * ``reconcile()`` — FS-as-source-of-truth recovery on startup
  * ``render_workspace_md(row)`` — pure template fill, used by scaffold + reconcile

File system is the source of truth (PRD L253). If DB and FS disagree, the
FS-backed workspace wins; DB is patched to match.
"""
from __future__ import annotations

import logging
import re
import shutil
import uuid
from datetime import datetime, timezone
from pathlib import Path
from string import Template
from typing import TYPE_CHECKING

from src.exceptions import BadRequestError, ConflictError, NotFoundError
from src.workspace_events import emit_and_deliver

if TYPE_CHECKING:
    from src.storage.registry import WorkspaceFileRegistry

logger = logging.getLogger(__name__)

# File layout under $WORKSPACES_ROOT/<slug>/
#   workspace.md  — index file (system-maintained; do NOT hand-edit)
#   designs/      — DesignDoc artifacts (populated Phase 3)
#   devworks/     — DevWork iteration notes (populated Phase 4)
# Subdirs under $WORKSPACES_ROOT/<slug>/ that the scaffold creates.
_SUBDIRS = ("designs", "devworks")

# Kebab-case slug: 1-63 chars, must start and end alphanumeric, no double dashes.
# Mirrors Docker/k8s name rules, which reject trailing dashes and `--`.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?!-)){0,61}[a-z0-9]$|^[a-z0-9]$")


def _load_template() -> Template:
    root = Path(__file__).resolve().parents[1]
    text = (root / "templates" / "workspace.md.tpl").read_text(encoding="utf-8")
    return Template(text)


def _replace_section(md: str, heading: str, new_body: str) -> str:
    """Replace the body between ``heading`` line and the next ``## `` heading.

    The heading line itself is kept; the previous body (including the
    italicised placeholder) is swapped for ``new_body``. Idempotent: if
    called twice with the same args, the output matches exactly.
    """
    lines = md.splitlines(keepends=False)
    out: list[str] = []
    i = 0
    while i < len(lines):
        line = lines[i]
        out.append(line)
        if line.strip() == heading:
            # Skip existing body until the next H2.
            j = i + 1
            while j < len(lines) and not lines[j].startswith("## "):
                j += 1
            out.append("")
            out.append(new_body)
            out.append("")
            i = j
            continue
        i += 1
    result = "\n".join(out)
    return result + "\n" if md.endswith("\n") else result


class WorkspaceManager:
    def __init__(
        self,
        db,
        project_root: Path | str | None = None,
        workspaces_root: Path | str | None = None,
        webhooks=None,
        registry: "WorkspaceFileRegistry | None" = None,
    ):
        self.db = db
        self.project_root = (
            Path(project_root) if project_root else Path(__file__).resolve().parents[1]
        )
        if workspaces_root is None:
            # Safe local default: $PROJECT/.coop/workspaces. Callers in production
            # should pass `settings.security.resolved_workspace_root()`.
            workspaces_root = self.project_root / ".coop" / "workspaces"
        self.workspaces_root = Path(workspaces_root).expanduser().resolve()
        self._template = _load_template()
        self.webhooks = webhooks
        self.registry = registry

    def _require_registry(self) -> "WorkspaceFileRegistry":
        if self.registry is None:
            raise BadRequestError("workspace registry not configured")
        return self.registry

    # ---- id / time helpers (Phase 1 unchanged) ----

    @staticmethod
    def _new_id() -> str:
        return f"ws-{uuid.uuid4().hex[:12]}"

    @staticmethod
    def _now() -> str:
        return datetime.now(timezone.utc).isoformat()

    # ---- path helpers ----

    def _slug_dir(self, slug: str) -> Path:
        return self.workspaces_root / slug

    def _assert_under_root(self, p: Path) -> None:
        resolved = p.resolve()
        root = self.workspaces_root.resolve()
        try:
            resolved.relative_to(root)
        except ValueError as exc:
            raise BadRequestError(f"path escapes workspaces_root: {p}") from exc

    # ---- rendering ----

    def render_workspace_md(
        self,
        *,
        workspace_id: str,
        title: str,
        slug: str,
        created_at: str,
        status: str,
    ) -> str:
        # string.Template.safe_substitute handles stray braces in title naturally.
        return self._template.safe_substitute(
            id=workspace_id,
            title=title,
            slug=slug,
            created_at=created_at,
            status=status,
        )

    # ---- Phase 1 DB-only APIs (kept for internal callers/tests) ----

    async def create(self, title: str, slug: str, root_path: str) -> str:
        wid = self._new_id()
        now = self._now()
        await self.db.execute(
            """INSERT INTO workspaces(id, title, slug, status, root_path, created_at, updated_at)
               VALUES(?,?,?,?,?,?,?)""",
            (wid, title, slug, "active", root_path, now, now),
        )
        return wid

    async def get(self, workspace_id: str) -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM workspaces WHERE id=?", (workspace_id,)
        )

    async def get_by_slug(self, slug: str) -> dict | None:
        return await self.db.fetchone(
            "SELECT * FROM workspaces WHERE slug=?", (slug,)
        )

    async def list(self, status: str | None = None) -> list[dict]:
        if status:
            return await self.db.fetchall(
                "SELECT * FROM workspaces WHERE status=? ORDER BY created_at DESC",
                (status,),
            )
        return await self.db.fetchall(
            "SELECT * FROM workspaces ORDER BY created_at DESC"
        )

    async def archive(self, workspace_id: str) -> int:
        now = self._now()
        return await self.db.execute_rowcount(
            "UPDATE workspaces SET status='archived', updated_at=? WHERE id=? AND status='active'",
            (now, workspace_id),
        )

    # ---- Phase 2: scaffold APIs ----

    async def create_with_scaffold(self, title: str, slug: str) -> dict:
        """Create DB record AND filesystem scaffold atomically.

        Order: FS first (mkdir, write md) then DB (INSERT). If FS fails,
        no orphan DB row. If DB fails, rollback FS (rmtree the new dir).
        """
        if not _SLUG_RE.match(slug):
            raise BadRequestError(
                f"invalid slug {slug!r} (expected kebab-case, 1-63 chars)"
            )

        slug_dir = self._slug_dir(slug)
        self._assert_under_root(slug_dir)

        if slug_dir.exists():
            raise ConflictError(
                f"workspace directory {slug!r} already exists on disk"
            )

        if await self.get_by_slug(slug):
            raise ConflictError(
                f"workspace with slug {slug!r} already exists in DB"
            )

        wid = self._new_id()
        now = self._now()
        md_content = self.render_workspace_md(
            workspace_id=wid, title=title, slug=slug, created_at=now, status="active"
        )

        registry = self._require_registry()

        # 1) FS scaffold — mkdir subdirs, then write workspace.md via the
        #    underlying store. We bypass the registry's 2-step put_bytes here
        #    because the `workspaces` row doesn't exist yet, so `repo.upsert`
        #    would fail the workspace_id FK. See Phase 3 plan Task 9 GOTCHA 1.
        try:
            slug_dir.mkdir(parents=True, exist_ok=False)
            for sub in _SUBDIRS:
                (slug_dir / sub).mkdir(exist_ok=False)
            await registry.store.put_bytes(
                f"{slug}/workspace.md", md_content.encode("utf-8"),
            )
        except OSError as exc:
            self._safe_rmtree(slug_dir)
            raise BadRequestError(
                f"failed to write workspace scaffold: {exc}"
            ) from exc

        # 2) DB insert; rollback FS on failure
        try:
            await self.db.execute(
                """INSERT INTO workspaces(id, title, slug, status, root_path, created_at, updated_at)
                   VALUES(?,?,?,?,?,?,?)""",
                (wid, title, slug, "active", str(slug_dir), now, now),
            )
        except Exception:
            self._safe_rmtree(slug_dir)
            raise

        # 3) Register the freshly-written workspace.md in workspace_files.
        #    Failure at this stage must compensate the DB INSERT + FS scaffold
        #    so the aggregate operation stays atomic.
        ws_row = {"id": wid, "slug": slug}
        try:
            await registry.index_existing(
                workspace_row=ws_row,
                relative_path="workspace.md",
                kind="workspace_md",
            )
        except Exception:
            try:
                await self.db.execute(
                    "DELETE FROM workspaces WHERE id=?", (wid,),
                )
            except Exception:
                logger.exception(
                    "rollback DELETE FROM workspaces failed for %s", wid
                )
            self._safe_rmtree(slug_dir)
            raise

        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="workspace.created",
            workspace_id=wid,
            correlation_id=wid,
            payload={"workspace_id": wid, "title": title, "slug": slug},
        )

        return {
            "id": wid,
            "title": title,
            "slug": slug,
            "status": "active",
            "root_path": str(slug_dir),
            "created_at": now,
            "updated_at": now,
        }

    async def archive_with_scaffold(self, workspace_id: str) -> bool:
        """Archive: DB status -> archived, rewrite workspace.md front-matter.

        Idempotent: returns True if state changed, False if already archived.
        Physical directory is NOT removed (human may want artifacts).
        """
        row = await self.get(workspace_id)
        if row is None:
            raise NotFoundError(f"workspace {workspace_id!r} not found")
        if row["status"] == "archived":
            return False

        changed = await self.archive(workspace_id)
        if not changed:
            return False

        await emit_and_deliver(
            self.db,
            self.webhooks,
            event_name="workspace.archived",
            workspace_id=workspace_id,
            correlation_id=workspace_id,
            payload={"workspace_id": workspace_id},
        )

        # Use the canonical slug dir under workspaces_root rather than the
        # stored root_path. Phase 1 legacy rows (created via the DB-only
        # `create()` API) may have arbitrary root_paths that predate the
        # workspaces_root invariant; refusing to archive them would be a
        # regression. Phase 2 rows always store `workspaces_root/<slug>`
        # here, so the two paths match.
        registry = self._require_registry()
        ref = await registry.stat(
            workspace_slug=row["slug"], relative_path="workspace.md",
        )
        if ref is not None:
            refreshed = self.render_workspace_md(
                workspace_id=row["id"],
                title=row["title"],
                slug=row["slug"],
                created_at=row["created_at"],
                status="archived",
            )
            await registry.put_markdown(
                workspace_row=row, relative_path="workspace.md",
                text=refreshed, kind="workspace_md",
            )
        else:
            logger.warning(
                "workspace.md missing for %s — skipping rewrite", row["id"],
            )
        return True

    # ---- Phase 2: reconcile ----

    async def reconcile(self) -> dict:
        """Scan FS vs DB and patch differences (FS wins).

        Report shape matches ``WorkspaceSyncReport``:
          * ``fs_only``: slugs present on disk but absent in DB -> INSERT to DB
          * ``db_only``: DB rows whose root_path does not exist -> mark archived
          * ``in_sync``: present in both
        """
        fs_only: list[str] = []
        db_only: list[str] = []
        in_sync: list[str] = []

        self.workspaces_root.mkdir(parents=True, exist_ok=True)

        db_rows = await self.list()
        db_by_slug = {r["slug"]: r for r in db_rows}

        fs_slugs: set[str] = set()
        for entry in self.workspaces_root.iterdir():
            if not entry.is_dir():
                continue
            if not _SLUG_RE.match(entry.name):
                logger.warning(
                    "skipping non-conforming dir under workspaces_root: %s", entry
                )
                continue
            fs_slugs.add(entry.name)

        # FS-only: INSERT minimal DB row
        for slug in sorted(fs_slugs):
            if slug not in db_by_slug:
                md_path = self.workspaces_root / slug / "workspace.md"
                meta = self._parse_front_matter(md_path) if md_path.exists() else {}
                wid = meta.get("id") or self._new_id()
                title = meta.get("title") or slug
                created_at = meta.get("created_at") or self._now()
                now = self._now()
                try:
                    await self.db.execute(
                        """INSERT INTO workspaces(id, title, slug, status, root_path, created_at, updated_at)
                           VALUES(?,?,?,?,?,?,?)""",
                        (
                            wid,
                            title,
                            slug,
                            "active",
                            str(self.workspaces_root / slug),
                            created_at,
                            now,
                        ),
                    )
                    fs_only.append(slug)
                except Exception as exc:
                    logger.exception(
                        "reconcile: failed to INSERT slug=%s: %s", slug, exc
                    )

        # DB rows: either in-sync or drifted
        for slug, row in db_by_slug.items():
            if slug in fs_slugs:
                in_sync.append(row["id"])
                continue
            if row["status"] != "archived":
                await self.archive(row["id"])
                logger.warning(
                    "reconcile: workspace %s (%s) missing on disk -> archived",
                    row["id"],
                    slug,
                )
            db_only.append(row["id"])

        return {"fs_only": fs_only, "db_only": db_only, "in_sync": in_sync}

    # ---- helpers ----

    @staticmethod
    def _safe_rmtree(p: Path) -> None:
        try:
            if p.exists():
                shutil.rmtree(p)
        except OSError:
            logger.exception("failed to cleanup partial workspace dir %s", p)

    # ---- Phase 3: workspace.md section refresh ----

    async def refresh_workspace_md(self, workspace_id: str) -> None:
        """Rewrite workspace.md to reflect current DesignWork / DesignDoc state.

        Invoked by DesignWorkStateMachine after each state transition and by
        DesignDocManager after D6 PERSIST. Safe no-op if the workspace row is
        missing (another subsystem may have archived it concurrently).
        """
        ws = await self.get(workspace_id)
        if ws is None:
            logger.warning("refresh_workspace_md: workspace %s missing", workspace_id)
            return
        registry = self._require_registry()
        ref = await registry.stat(
            workspace_slug=ws["slug"], relative_path="workspace.md",
        )
        if ref is None:
            logger.warning(
                "refresh_workspace_md: workspace.md missing for %s", workspace_id,
            )
            return

        design_docs = await self.db.fetchall(
            "SELECT slug, version, status FROM design_docs "
            "WHERE workspace_id=? ORDER BY created_at",
            (workspace_id,),
        )
        design_works = await self.db.fetchall(
            "SELECT id, mode, current_state, loop, sub_slug, output_design_doc_id "
            "FROM design_works WHERE workspace_id=? ORDER BY created_at",
            (workspace_id,),
        )

        lines: list[str] = []
        for dd in design_docs:
            lines.append(
                f"- designs/DES-{dd['slug']}-{dd['version']}.md"
                f" — 状态：{dd['status']}"
            )
        for dw in design_works:
            # Skip already-represented DesignWorks (their design_doc is in the list).
            if dw["output_design_doc_id"]:
                continue
            slug_part = dw["sub_slug"] or dw["id"]
            lines.append(
                f"- design_work {dw['id']} "
                f"(slug={slug_part}) · state={dw['current_state']} · loop={dw['loop']}"
            )
        if lines:
            designs_section = "\n".join(lines)
        else:
            designs_section = (
                "_暂无 DesignWork。在此 Workspace 下创建后此处自动刷新。_"
            )

        dev_works = await self.db.fetchall(
            "SELECT id, design_doc_id, current_step, iteration_rounds, last_score "
            "FROM dev_works WHERE workspace_id=? ORDER BY created_at",
            (workspace_id,),
        )
        dev_lines: list[str] = []
        for d in dev_works:
            last_score = (
                d["last_score"] if d["last_score"] is not None else "—"
            )
            dev_lines.append(
                f"- devworks/DEV-{d['id']}/ — design={d['design_doc_id']} · "
                f"step={d['current_step']} · round={d['iteration_rounds']} · "
                f"score={last_score}"
            )
        dev_section = (
            "\n".join(dev_lines) if dev_lines else "_暂无 DevWork。_"
        )

        text = await registry.read_text(
            workspace_slug=ws["slug"], relative_path="workspace.md",
        )
        new_text = _replace_section(text, "## 设计工作", designs_section)
        new_text = _replace_section(new_text, "## 开发工作", dev_section)
        await registry.put_markdown(
            workspace_row=ws, relative_path="workspace.md",
            text=new_text, kind="workspace_md",
        )

    @staticmethod
    def _parse_front_matter(md: Path) -> dict[str, str]:
        """Minimal YAML front-matter parser for reconcile recovery.

        Intentionally simple: only extracts ``key: value`` pairs between the
        opening and closing ``---`` lines. Sufficient for the fields our
        template writes. Values are length-capped and stripped of control
        characters — a crafted ``workspace.md`` on disk should not be able
        to push unbounded content into the DB.
        """
        try:
            text = md.read_text(encoding="utf-8")
        except OSError:
            return {}
        if not text.startswith("---"):
            return {}
        lines = text.splitlines()[1:]
        out: dict[str, str] = {}
        for line in lines:
            if line.strip() == "---":
                break
            if ":" in line:
                k, _, v = line.partition(":")
                cleaned = "".join(
                    c for c in v.strip() if c == " " or not c.isspace() and c.isprintable()
                )
                out[k.strip()] = cleaned[:120]
        return out
