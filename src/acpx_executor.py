"""Minimum AcpxExecutor — one-shot local subprocess runs for DesignWork / DevWork.

Workspace-era state machines drive agents synchronously via ``run_once``. No
host pool, no job lifecycle, no tracing. SSH dispatch / concurrency control,
if reintroduced, will be rebuilt against the workspace data model — not
resurrected from the legacy Run-centric stack.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path


class AcpxExecutor:
    def __init__(self, db, webhook_notifier, config=None, coop_dir=".coop", project_root=None):
        self.db = db
        self.webhooks = webhook_notifier
        self.config = config
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
        coop_path = Path(coop_dir)
        if not coop_path.is_absolute():
            coop_path = self.project_root / coop_path
        self.coop_dir = str(coop_path)

    # ------------------------------------------------------------------
    # Helpers (preserved from the original command builder)
    # ------------------------------------------------------------------

    def _acpx_cfg(self):
        return getattr(self.config, "acpx", None) if self.config else None

    def _permission_flag(self):
        cfg = self._acpx_cfg()
        mode = cfg.permission_mode if cfg else "approve-all"
        return {
            "approve-all": "--approve-all",
            "approve-reads": "--approve-reads",
            "deny-all": "--deny-all",
        }.get(mode, "--approve-all")

    def _resolve_agent(self, agent_type):
        return "claude" if agent_type == "claude" else "codex"

    def _normalize_task_file(self, task_file):
        if not task_file:
            return None
        return os.path.abspath(task_file)

    def _build_acpx_exec_cmd(self, agent_type, worktree, timeout_sec, task_file=None, prompt=None):
        agent = self._resolve_agent(agent_type)
        task_file = self._normalize_task_file(task_file)
        cmd = [
            "acpx", "--cwd", worktree,
            "--format", "json",
            self._permission_flag(),
            "--timeout", str(timeout_sec),
        ]
        cfg = self._acpx_cfg()
        if cfg:
            if getattr(cfg, "json_strict", False):
                cmd.append("--json-strict")
            if getattr(cfg, "model", None):
                cmd += ["--model", cfg.model]
        cmd += [agent, "exec"]
        if task_file:
            cmd += ["--file", task_file]
        elif prompt:
            cmd.append(prompt)
        return cmd

    # ------------------------------------------------------------------
    # Public API — the single call path every workspace SM uses
    # ------------------------------------------------------------------

    async def run_once(
        self,
        agent_type: str,
        worktree: str,
        timeout_sec: int,
        task_file: str | None = None,
        prompt: str | None = None,
    ) -> tuple[str, int]:
        """Run ``acpx <agent> exec`` once against ``worktree``.

        Returns ``(stdout_text, exit_code)``. No persistent session, no job
        row, no callbacks. Callers own retry / status interpretation.
        """
        cmd = self._build_acpx_exec_cmd(agent_type, worktree, timeout_sec, task_file, prompt)
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=worktree,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, _ = await proc.communicate()
        return stdout.decode("utf-8", errors="replace").strip(), proc.returncode
