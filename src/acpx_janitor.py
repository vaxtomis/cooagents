"""Host-local cleanup loop for cooagents-owned acpx executions.

The janitor never kills by process name. It only acts on ``agent_executions``
rows whose lease expired and whose live process environment still carries the
matching ``COOAGENTS_RUN_TOKEN``.
"""
from __future__ import annotations

import asyncio
import logging
import os
import signal
from pathlib import Path
from typing import Any

from src.llm_runner import Session
from src.models import LOCAL_HOST_ID

logger = logging.getLogger(__name__)


def _read_proc_environ(pid: int) -> dict[str, str]:
    try:
        data = Path(f"/proc/{pid}/environ").read_bytes()
    except OSError:
        return {}
    env: dict[str, str] = {}
    for raw in data.split(b"\0"):
        if not raw or b"=" not in raw:
            continue
        key, value = raw.split(b"=", 1)
        try:
            env[key.decode()] = value.decode(errors="replace")
        except UnicodeDecodeError:
            continue
    return env


def _pid_starttime(pid: int) -> str | None:
    try:
        data = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
        tail = data.rsplit(") ", 1)[1].split()
        return tail[19]
    except (OSError, IndexError, ValueError):
        return None


def _pid_cwd(pid: int) -> Path | None:
    try:
        return Path(f"/proc/{pid}/cwd").resolve()
    except OSError:
        return None


def _pid_pgid(pid: int) -> int | None:
    try:
        return os.getpgid(pid)
    except (ProcessLookupError, PermissionError):
        return None


class AcpxJanitor:
    """Periodic cleanup for one host's expired agent executions."""

    def __init__(
        self,
        *,
        execution_repo: Any,
        llm_runner: Any,
        workspaces_root: str | Path,
        host_id: str = LOCAL_HOST_ID,
        interval_s: int = 60,
        terminate_grace_s: int = 15,
        kill_grace_s: int = 10,
        kill_enabled: bool = True,
    ) -> None:
        self.execution_repo = execution_repo
        self.llm_runner = llm_runner
        self.workspaces_root = Path(workspaces_root).resolve()
        self.host_id = host_id
        self.interval_s = interval_s
        self.terminate_grace_s = terminate_grace_s
        self.kill_grace_s = kill_grace_s
        self.kill_enabled = kill_enabled
        self._task: asyncio.Task | None = None

    def start(self) -> None:
        if self._task is not None and not self._task.done():
            return
        self._task = asyncio.create_task(self._run(), name=f"acpx-janitor:{self.host_id}")

    async def stop(self) -> None:
        if self._task is None:
            return
        self._task.cancel()
        try:
            await asyncio.gather(self._task, return_exceptions=True)
        finally:
            self._task = None

    async def _run(self) -> None:
        while True:
            try:
                await self.cleanup_once()
            except asyncio.CancelledError:
                raise
            except Exception:
                logger.exception("acpx janitor iteration failed")
            try:
                await asyncio.sleep(self.interval_s)
            except asyncio.CancelledError:
                raise

    async def cleanup_once(self) -> None:
        if os.name == "nt":
            return
        rows = await self.execution_repo.list_expired_for_host(self.host_id)
        for row in rows:
            try:
                await self._cleanup_row(row)
            except Exception:
                logger.exception("acpx janitor cleanup failed for %s", row.get("id"))

    def _tagged_pids(self, row: dict[str, Any]) -> list[int]:
        token = str(row.get("run_token") or "")
        if not token:
            return []
        expected_pgid = row.get("pgid")
        candidates: set[int] = set()
        if row.get("pid") is not None:
            try:
                candidates.add(int(row["pid"]))
            except (TypeError, ValueError):
                pass
        proc_root = Path("/proc")
        try:
            proc_entries = list(proc_root.iterdir())
        except OSError:
            proc_entries = []
        for entry in proc_entries:
            if not entry.name.isdigit():
                continue
            pid = int(entry.name)
            if expected_pgid is not None and _pid_pgid(pid) != int(expected_pgid):
                continue
            env = _read_proc_environ(pid)
            if env.get("COOAGENTS_RUN_TOKEN") == token:
                candidates.add(pid)
        return sorted(candidates)

    def _validate_row(self, row: dict[str, Any]) -> tuple[bool, list[int], str]:
        pids = self._tagged_pids(row)
        if not pids:
            return False, [], "no tagged live process"
        direct_pid = row.get("pid")
        if direct_pid is not None:
            try:
                direct_pid_i = int(direct_pid)
            except (TypeError, ValueError):
                direct_pid_i = None
            if direct_pid_i in pids and row.get("pid_starttime"):
                if _pid_starttime(direct_pid_i) != str(row["pid_starttime"]):
                    return False, pids, "pid starttime mismatch"
        for pid in pids:
            cwd = _pid_cwd(pid)
            if cwd is None:
                continue
            try:
                cwd.relative_to(self.workspaces_root)
            except ValueError:
                return False, pids, f"cwd outside workspace root: {cwd}"
        return True, pids, "ok"

    async def _cleanup_row(self, row: dict[str, Any]) -> None:
        ok, pids, reason = self._validate_row(row)
        if not ok:
            logger.warning(
                "acpx janitor skipped execution=%s: %s",
                row.get("id"), reason,
            )
            return

        await self.execution_repo.mark_cleanup_started(
            row["id"], reason="lease expired",
        )
        session_name = row.get("session_name")
        if session_name:
            session = Session(
                name=session_name,
                anchor_cwd=row["cwd"],
                agent=row["agent"],
                created_at=row["started_at"],
            )
            try:
                await self.llm_runner.delete_session(session)
            except Exception:
                logger.warning(
                    "acpx janitor session close failed for %s",
                    session_name,
                    exc_info=True,
                )

        if not self.kill_enabled:
            await self.execution_repo.mark_state(
                row["id"], state="stale", cleanup_reason="kill disabled",
            )
            return

        pgid = row.get("pgid")
        if pgid is None:
            pgid = _pid_pgid(pids[0])
        if pgid is None:
            await self.execution_repo.mark_state(
                row["id"], state="abandoned", cleanup_reason="missing pgid",
            )
            return

        try:
            os.killpg(int(pgid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            await self.execution_repo.mark_state(
                row["id"], state="terminated", cleanup_reason="already gone",
            )
            return

        await asyncio.sleep(self.terminate_grace_s)
        still_live = [pid for pid in pids if Path(f"/proc/{pid}").exists()]
        if not still_live:
            await self.execution_repo.mark_state(
                row["id"], state="terminated", cleanup_reason="SIGTERM",
            )
            return

        try:
            os.killpg(int(pgid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            pass
        await asyncio.sleep(self.kill_grace_s)
        await self.execution_repo.mark_state(
            row["id"], state="killed", cleanup_reason="SIGKILL",
        )
