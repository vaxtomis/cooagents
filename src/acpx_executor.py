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
import signal
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

    def _build_codex_exec_cmd(self, worktree: str) -> list[str]:
        """Build the direct Codex CLI command for local one-shot runs.

        ``acpx codex exec`` does not expose Codex's sandbox flags, so local
        DesignWork would inherit a read-only default on untrusted worktrees.
        Direct ``codex exec`` lets us enforce the non-interactive full-access
        mode required for background artifact generation.
        """
        cfg = self._acpx_cfg()
        cmd = [
            "codex",
            "exec",
            "--json",
            "--skip-git-repo-check",
            "--sandbox",
            "danger-full-access",
            "--ask-for-approval",
            "never",
            "--cd",
            worktree,
        ]
        if cfg and getattr(cfg, "model", None):
            cmd += ["--model", cfg.model]
        cmd.append("-")
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
        normalized_task_file = self._normalize_task_file(task_file)
        cmd = self._build_acpx_exec_cmd(
            agent_type, worktree, timeout_sec, normalized_task_file, prompt
        )
        if host_id == LOCAL_HOST_ID:
            if self._resolve_agent(agent_type) == "codex":
                self._ensure_worktree_writable(worktree)
                stdin_text = self._load_prompt_text(normalized_task_file, prompt)
                return await self._run_local(
                    self._build_codex_exec_cmd(worktree),
                    worktree,
                    stdin_text=stdin_text,
                    timeout_sec=timeout_sec,
                    cleanup_process_group=True,
                )
            return await self._run_local(
                cmd, worktree, cleanup_process_group=True
            )
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

    @staticmethod
    def _load_prompt_text(task_file: str | None, prompt: str | None) -> str:
        if task_file:
            return Path(task_file).read_text(encoding="utf-8")
        return prompt or ""

    @staticmethod
    def _ensure_worktree_writable(worktree: str) -> None:
        """Create/repair local write access before launching Codex.

        This is intentionally limited to the execution cwd. It does not
        broaden permissions outside the workspace.
        """
        path = Path(worktree)
        path.mkdir(parents=True, exist_ok=True)
        probe = path / ".cooagents-write-check"
        try:
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)
            return
        except PermissionError:
            if os.name != "nt":
                path.chmod(path.stat().st_mode | 0o700)
            probe.write_text("ok", encoding="utf-8")
            probe.unlink(missing_ok=True)

    @staticmethod
    def _terminate_process_group(pid: int) -> None:
        if os.name == "nt":
            return
        try:
            os.killpg(pid, signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            return

    @staticmethod
    async def _wait_for_direct_exit(proc, timeout_sec: int | None) -> int:
        """Return when the direct child exits, even if descendants hold pipes."""
        wait_task = asyncio.create_task(proc.wait())
        loop = asyncio.get_running_loop()
        deadline = (
            None if timeout_sec is None else loop.time() + float(timeout_sec)
        )
        try:
            while True:
                if proc.returncode is not None:
                    return proc.returncode
                if wait_task.done():
                    return await wait_task
                if deadline is not None:
                    remaining = deadline - loop.time()
                    if remaining <= 0:
                        raise asyncio.TimeoutError
                    await asyncio.sleep(min(0.05, remaining))
                else:
                    await asyncio.sleep(0.05)
        finally:
            if not wait_task.done():
                wait_task.cancel()
                await asyncio.gather(wait_task, return_exceptions=True)

    async def _run_local(
        self,
        cmd: list[str],
        worktree: str,
        *,
        stdin_text: str | None = None,
        timeout_sec: int | None = None,
        cleanup_process_group: bool = False,
    ) -> tuple[str, int]:
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=worktree,
            stdin=(
                asyncio.subprocess.PIPE
                if stdin_text is not None
                else asyncio.subprocess.DEVNULL
            ),
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
            proc_stdin = getattr(proc, "stdin", None)
            if stdin_text is not None and proc_stdin is not None:
                proc_stdin.write(stdin_text.encode("utf-8"))
                await proc_stdin.drain()
                proc_stdin.close()
            await self._wait_for_direct_exit(proc, timeout_sec)
            if cleanup_process_group:
                pid = getattr(proc, "pid", None)
                if pid is not None:
                    self._terminate_process_group(pid)
            await asyncio.sleep(0)
        except asyncio.TimeoutError:
            if cleanup_process_group:
                pid = getattr(proc, "pid", None)
                if pid is not None:
                    self._terminate_process_group(pid)
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            raise
        finally:
            for task in (stdout_task, stderr_task):
                task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
        return (
            b"".join(stdout_chunks).decode("utf-8", errors="replace").strip(),
            proc.returncode if proc.returncode is not None else -1,
        )
