"""Workspace lifecycle — DB records + filesystem scaffolding + reconcile.

cooagents is the sole writer of every workspace row and every workspace
file. ``regenerate_workspace_md`` is a single render-from-DB and write
through the registry; ``reconcile`` is single-mode FS-wins.

File system remains the source of truth for the *existence* of a workspace
(PRD §Reconcile). If DB and FS disagree on which workspaces exist,
``reconcile`` takes FS-wins. The *contents* of individual workspace_files
are governed by ``WorkspaceFileRegistry.register`` (local atomic write →
PUT OSS when enabled → DB upsert).
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
_SUBDIRS = ("designs", "devworks")

# Kebab-case slug: 1-63 chars, must start and end alphanumeric, no double dashes.
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9]|-(?!-)){0,61}[a-z0-9]$|^[a-z0-9]$")

_DEFAULT_DESIGN_SECTION = (
    "_暂无 DesignWork。在此 Workspace 下创建后此处自动刷新。_"
)
_DEFAULT_DEV_SECTION = "_暂无 DevWork。在此 Workspace 下创建后此处自动刷新。_"


def _load_template() -> Template:
    root = Path(__file__).resolve().parents[1]
    text = (root / "templates" / "workspace.md.tpl").read_text(encoding="utf-8")
    return Template(text)


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
        design_section: str = _DEFAULT_DESIGN_SECTION,
        dev_section: str = _DEFAULT_DEV_SECTION,
    ) -> str:
        return self._template.safe_substitute(
            id=workspace_id,
            title=title,
            slug=slug,
            created_at=created_at,
            status=status,
            design_section=design_section,
            dev_section=dev_section,
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
        """Create DB record AND filesystem scaffold atomically."""
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
        #    underlying store. We bypass the registry here because the
        #    `workspaces` row doesn't exist yet, so `repo.upsert` would fail
        #    the workspace_id FK. See Phase 3 plan Task 9 GOTCHA 1.
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
        """Archive: DB status -> archived, rewrite workspace.md front-matter."""
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

        registry = self._require_registry()
        ref = await registry.stat(
            workspace_slug=row["slug"], relative_path="workspace.md",
        )
        if ref is not None:
            refreshed = await self._render_workspace_md_from_db({
                **row, "status": "archived",
            })
            await registry.put_markdown(
                workspace_row=row, relative_path="workspace.md",
                text=refreshed, kind="workspace_md",
            )
        else:
            logger.warning(
                "workspace.md missing for %s — skipping rewrite", row["id"],
            )
        return True

    # ---- reconcile (single-mode FS-wins) ----

    async def reconcile(self) -> dict:
        """Scan FS vs DB and patch differences (FS wins)."""
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

    # ---- DB-derived workspace.md regeneration (single render+write) ----

    async def _render_workspace_md_from_db(self, ws: dict) -> str:
        """Render the full workspace.md from DB state.

        Pure function of (workspace row, design_docs, design_works, dev_works).
        """
        workspace_id = ws["id"]

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
            if dw["output_design_doc_id"]:
                continue
            slug_part = dw["sub_slug"] or dw["id"]
            lines.append(
                f"- design_work {dw['id']} "
                f"(slug={slug_part}) · state={dw['current_state']} · loop={dw['loop']}"
            )
        design_section = (
            "\n".join(lines) if lines else _DEFAULT_DESIGN_SECTION
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
            "\n".join(dev_lines) if dev_lines else _DEFAULT_DEV_SECTION
        )

        return self.render_workspace_md(
            workspace_id=ws["id"],
            title=ws["title"],
            slug=ws["slug"],
            created_at=ws["created_at"],
            status=ws["status"],
            design_section=design_section,
            dev_section=dev_section,
        )

    async def regenerate_workspace_md(self, workspace_id: str) -> dict:
        """Re-render workspace.md from DB and write via the registry.

        Single-process call; no retry loop. Internal callers (state-machine
        transitions, scaffold) drive this; no operator HTTP route exposes it.
        """
        ws = await self.get(workspace_id)
        if ws is None:
            logger.warning(
                "regenerate_workspace_md: workspace %s missing", workspace_id,
            )
            return {"skipped": "missing_workspace"}
        text = await self._render_workspace_md_from_db(ws)
        await self._require_registry().put_markdown(
            workspace_row=ws,
            relative_path="workspace.md",
            text=text,
            kind="workspace_md",
        )
        return {"skipped": None}

    @staticmethod
    def _parse_front_matter(md: Path) -> dict[str, str]:
        """Minimal YAML front-matter parser for reconcile recovery."""
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
