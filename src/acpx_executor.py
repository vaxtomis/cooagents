"""Minimum AcpxExecutor — one-shot local subprocess runs for DesignWork / DevWork.

Workspace-era state machines drive agents synchronously via ``run_once``. No
host pool, no job lifecycle, no tracing. SSH dispatch / concurrency control,
if reintroduced, will be rebuilt against the workspace data model — not
resurrected from the legacy Run-centric stack.

Phase 2 note: :class:`LLMRunner` (``src/llm_runner.py``) is the new public
surface for cooagents → acpx. ``AcpxExecutor`` survives as the
local-execution implementation behind ``LLMRunner.run_oneshot`` and is
slated for removal in Phase 7 cleanup.
"""
from __future__ import annotations

import asyncio
import os
from pathlib import Path
from typing import TYPE_CHECKING

from src.models import LOCAL_HOST_ID

if TYPE_CHECKING:
    from src.agent_hosts.ssh_dispatcher import SshDispatcher


class AcpxExecutor:
    def __init__(
        self,
        db,
        webhook_notifier,
        config=None,
        coop_dir=".coop",
        project_root=None,
        *,
        ssh_dispatcher: "SshDispatcher | None" = None,
    ):
        self.db = db
        self.webhooks = webhook_notifier
        self.config = config
        self.project_root = Path(project_root) if project_root else Path(__file__).resolve().parents[1]
        coop_path = Path(coop_dir)
        if not coop_path.is_absolute():
            coop_path = self.project_root / coop_path
        self.coop_dir = str(coop_path)
        # Phase 8a: optional remote dispatcher. When None, every host_id !=
        # 'local' raises RuntimeError instead of attempting SSH.
        self.ssh_dispatcher = ssh_dispatcher

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
        *,
        host_id: str = LOCAL_HOST_ID,
        workspace_id: str | None = None,
        correlation_id: str | None = None,
    ) -> tuple[str, int]:
        """Run ``acpx <agent> exec`` once against ``worktree``.

        Returns ``(stdout_text, exit_code)``. ``host_id="local"`` keeps the
        Phase 7b behaviour byte-for-byte. Any other host id is delegated to
        the injected :class:`SshDispatcher`, which in Phase 8a only knows
        how to raise ``NotImplementedError`` — actual remote execution
        lands in Phase 8b.
        """
        cmd = self._build_acpx_exec_cmd(
            agent_type, worktree, timeout_sec, task_file, prompt
        )
        if host_id == LOCAL_HOST_ID:
            return await self._run_local(cmd, worktree)
        if self.ssh_dispatcher is None:
            raise RuntimeError(
                f"AcpxExecutor has no ssh_dispatcher; cannot dispatch to "
                f"host_id={host_id!r}"
            )
        # The remote worker recomputes WORKSPACES_ROOT itself, so it needs a
        # workspace-relative task_file rather than the local absolute path.
        remote_task_file = self._derive_remote_task_file(worktree, task_file)
        return await self.ssh_dispatcher.run_remote(
            host_id,
            cmd=cmd,
            cwd=worktree,
            timeout=timeout_sec,
            workspace_id=workspace_id,
            correlation_id=correlation_id,
            task_file=remote_task_file,
            agent=self._resolve_agent(agent_type),
        )

    @staticmethod
    def _derive_remote_task_file(
        worktree: str, task_file: str | None
    ) -> str | None:
        """Convert a possibly-absolute ``task_file`` to a path relative to
        ``worktree`` (workspace root). The remote worker treats it as a
        workspace-relative POSIX path.
        """
        if not task_file:
            return None
        try:
            rel = os.path.relpath(task_file, start=worktree)
        except ValueError:
            return task_file
        return rel.replace(os.sep, "/")

    async def _run_local(
        self, cmd: list[str], worktree: str
    ) -> tuple[str, int]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=worktree,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        stdout_chunks: list[bytes] = []
        stderr_chunks: list[bytes] = []

        async def _pump_stream(
            reader: asyncio.StreamReader | None, sink: list[bytes],
        ) -> None:
            if reader is None:
                return
            try:
                while True:
                    chunk = await reader.read(65536)
                    if not chunk:
                        return
                    sink.append(chunk)
            except asyncio.CancelledError:
                # The direct acpx process can exit after writing the final
                # result while a detached descendant still holds the pipe fd.
                # Preserve bytes collected so far and let the state machine
                # continue from the direct process return code.
                return

        stdout_task = asyncio.create_task(_pump_stream(proc.stdout, stdout_chunks))
        stderr_task = asyncio.create_task(_pump_stream(proc.stderr, stderr_chunks))
        try:
            await proc.wait()
            await asyncio.sleep(0)
        finally:
            for task in (stdout_task, stderr_task):
                task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        return (
            b"".join(stdout_chunks).decode("utf-8", errors="replace").strip(),
            proc.returncode if proc.returncode is not None else -1,
        )
