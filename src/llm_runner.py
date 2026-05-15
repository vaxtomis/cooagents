"""LLMRunner — single entrypoint for cooagents -> acpx LLM processes.

Phase 2 of the DevWork × acpx integration overhaul. Owns the acpx command
surface (`exec`, `sessions ensure / close / list / prune`, `prompt --session`,
`status --session`, `cancel --session`) so that DevWork / DesignWork state
machines never have to know whether they are in one-shot or session mode.

Contract is derived from the Phase 1 spike report:
``.claude/PRPs/reports/devwork-acpx-phase1-spike-report.md``.

Spike-derived invariants:
  * Sessions are bound to the cwd they were created from. ``prompt --session``
    from a different cwd returns ``NO_SESSION``. The :class:`Session` token
    captures ``anchor_cwd`` so callers cannot recompute it.
  * ``sessions ensure --name <X>`` is mandatory before any
    ``prompt --session <X>``.
  * Destroy is two-step: ``cancel`` (best-effort) → ``close`` (must use the
    anchor cwd) → background ``sessions prune --before <iso>`` (deferred).
    ``--older-than 0`` is invalid; only ``--before <iso>`` is supported.
  * ``sessions list --format json`` returns an array of objects with
    ``name``, ``cwd``, ``closed``, ``createdAt`` ... — used by the boot-time
    orphan sweep.

Phase 2 ships the surface only. DevWork / DesignWork still call
:meth:`run_oneshot`; ``start_session`` / ``prompt_session`` / ``delete_session``
gain real callers in Phase 4+.
"""
from __future__ import annotations

import asyncio
import json
import logging
import os
import signal
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Awaitable, Callable

logger = logging.getLogger(__name__)


# ---- session naming -------------------------------------------------------

DW_SESSION_PREFIX = "dw-"
DESIGN_SESSION_PREFIX = "design-"

_DW_ROLES = ("plan", "build", "review")

# Agents the boot-time orphan sweep walks. Matches the agent set
# AcpxExecutor.run_once knows how to resolve.
_SWEEP_AGENTS: tuple[str, ...] = ("claude", "codex")


def dw_session_name(dev_id: str, round_n: int, role: str) -> str:
    """Compose a deterministic DevWork session name.

    role must be one of {'plan', 'build', 'review'}.
    """
    assert role in _DW_ROLES, f"unknown DevWork session role: {role!r}"
    return f"{DW_SESSION_PREFIX}{dev_id}-r{round_n}-{role}"


# ---- types ---------------------------------------------------------------

@dataclass(frozen=True)
class Session:
    """Immutable session token returned by :meth:`LLMRunner.start_session`.

    ``anchor_cwd`` is the cwd the session was created from. All later
    ``prompt --session`` / ``close`` calls must use this same cwd or acpx
    will report ``NO_SESSION`` (spike Q(a)/Q(c)).
    """

    name: str
    anchor_cwd: str
    agent: str
    created_at: str


class IdleTimeoutError(RuntimeError):
    """Raised when no heartbeat advances within ``idle_window_s``.

    Phase 3: in oneshot mode the only "advance" signal is "subprocess
    still running", so this fires only when acpx itself wedges (zombie /
    parent-loss). Phase 4 plugs ``acpx status --session`` into the
    heartbeat callback and the predicate becomes LLM-aware.
    """

    def __init__(self, *, step_tag: str, idle_window_s: int) -> None:
        super().__init__(f"idle_timeout: {step_tag} ({idle_window_s}s)")
        self.step_tag = step_tag
        self.idle_window_s = idle_window_s


@dataclass(frozen=True)
class ProgressTick:
    """One heartbeat tick captured by :meth:`LLMRunner.run_with_progress`."""

    ts: str          # ISO8601 wall-clock when the tick fired
    elapsed_s: int   # seconds since the subprocess was spawned


HeartbeatCallback = Callable[[ProgressTick], Awaitable[None]]
AdvanceProbe = Callable[[], Awaitable[bool]]


class SessionLifecycleError(RuntimeError):
    """Raised when sessions ensure / close / prune / list returns rc!=0.

    Callers can distinguish lifecycle bookkeeping failure (this exception)
    from the LLM call itself failing (which surfaces as rc!=0 on the
    returned tuple).
    """

    def __init__(self, op: str, rc: int, stderr_tail: str) -> None:
        super().__init__(f"sessions {op} returned rc={rc}: {stderr_tail!r}")
        self.op = op
        self.rc = rc
        self.stderr_tail = stderr_tail


def _default_clock() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---- runner ---------------------------------------------------------------

class LLMRunner:
    """Single LLM-process entrypoint for cooagents.

    Phase 2 surface:
      * :meth:`run_oneshot` — delegates to ``AcpxExecutor.run_once``.
      * :meth:`start_session`, :meth:`prompt_session`, :meth:`status_session`,
        :meth:`cancel_session`, :meth:`delete_session` — session lifecycle.
      * :meth:`orphan_sweep_at_boot` — boot-time reaper.

    ``executor`` is typed ``Any`` at runtime to avoid a cycle with
    ``src.acpx_executor`` (which itself is wired up by ``src.app``).
    """

    def __init__(
        self,
        executor: Any,
        config: Any = None,
        *,
        clock: Callable[[], str] | None = None,
        monotonic: Callable[[], float] | None = None,
        agent_execution_repo: Any | None = None,
    ) -> None:
        self._executor = executor
        self._config = config
        self._clock = clock or _default_clock
        self._agent_execution_repo = agent_execution_repo
        # Phase 3: injectable monotonic clock so heartbeat tests can advance
        # wall-time deterministically without sleeping.
        self._monotonic = monotonic or time.monotonic

    # ---- helpers (mirror AcpxExecutor) -----------------------------------

    def _acpx_cfg(self):
        cfg = self._config
        if cfg is None:
            cfg = getattr(self._executor, "config", None)
        return getattr(cfg, "acpx", None) if cfg else None

    def _permission_flag(self) -> str:
        cfg = self._acpx_cfg()
        mode = cfg.permission_mode if cfg else "approve-all"
        return {
            "approve-all": "--approve-all",
            "approve-reads": "--approve-reads",
            "deny-all": "--deny-all",
        }.get(mode, "--approve-all")

    def _resolve_agent(self, agent_type: str) -> str:
        # Single source of truth lives on AcpxExecutor; delegate to avoid drift.
        return self._executor._resolve_agent(agent_type)

    def _common_flags(self, cwd: str) -> list[str]:
        """Shared head used by every session-mode acpx invocation.

        Mirrors the prefix in
        :meth:`AcpxExecutor._build_acpx_exec_cmd` minus the ``--timeout``
        bit (subcommands without timeouts — like ``sessions ensure`` —
        omit it; subcommands with timeouts append it themselves).
        """
        cmd = ["acpx", "--cwd", cwd, "--format", "json", self._permission_flag()]
        cfg = self._acpx_cfg()
        if cfg:
            if getattr(cfg, "json_strict", False):
                cmd.append("--json-strict")
            if getattr(cfg, "model", None):
                cmd += ["--model", cfg.model]
        return cmd

    # ---- command builders ------------------------------------------------

    def _build_oneshot_cmd(
        self,
        agent_type: str,
        worktree: str,
        timeout_sec: int,
        task_file: str | None = None,
        prompt: str | None = None,
    ) -> list[str]:
        # Delegate to AcpxExecutor so the byte-for-byte shape never drifts.
        return self._executor._build_acpx_exec_cmd(
            agent_type, worktree, timeout_sec, task_file, prompt
        )

    def _build_ensure_cmd(self, name: str, anchor_cwd: str, agent: str) -> list[str]:
        return [*self._common_flags(anchor_cwd), agent, "sessions", "ensure", "--name", name]

    def _build_set_mode_cmd(
        self, name: str, anchor_cwd: str, agent: str, mode: str,
    ) -> list[str]:
        return [
            *self._common_flags(anchor_cwd),
            agent, "set-mode", "--session", name, mode,
        ]

    def _build_prompt_cmd(
        self,
        session: Session,
        *,
        text: str | None,
        task_file: str | None,
        timeout_sec: int | None,
    ) -> list[str]:
        assert (text is None) != (task_file is None), (
            "prompt_session requires exactly one of text or task_file"
        )
        cmd = self._common_flags(session.anchor_cwd)
        if timeout_sec is not None:
            cmd += ["--timeout", str(timeout_sec)]
        cmd += [session.agent, "prompt", "--session", session.name]
        if task_file is not None:
            cmd += ["--file", task_file]
        else:
            cmd.append(text)  # type: ignore[arg-type]
        return cmd

    def _build_status_cmd(self, session: Session) -> list[str]:
        return [
            *self._common_flags(session.anchor_cwd),
            session.agent, "status", "--session", session.name,
        ]

    def _build_cancel_cmd(self, session: Session) -> list[str]:
        return [
            *self._common_flags(session.anchor_cwd),
            session.agent, "cancel", "--session", session.name,
        ]

    def _build_close_cmd(self, session: Session) -> list[str]:
        # spike Q(c): close MUST run from the anchor cwd.
        # Phase 11: acpx 0.6.x takes the session name as a positional
        # argument, not --name. Verified live on host (acpx 0.6.1):
        #   $ acpx codex sessions close --help
        #   Usage: acpx codex sessions close [options] [name]
        # Passing --name returns rc=1 with empty stderr (silent failure).
        return [
            *self._common_flags(session.anchor_cwd),
            session.agent, "sessions", "close", session.name,
        ]

    def _build_list_cmd(self, agent: str, cwd: str) -> list[str]:
        return [*self._common_flags(cwd), agent, "sessions", "list"]

    # ---- subprocess plumbing --------------------------------------------

    @staticmethod
    def _pid_starttime(pid: int) -> str | None:
        if os.name == "nt":
            return None
        try:
            stat = (f"/proc/{pid}/stat")
            data = open(stat, "r", encoding="utf-8").read()
        except OSError:
            return None
        try:
            # comm can contain spaces and is wrapped in parentheses. The
            # starttime field is the 22nd token, i.e. index 19 after ") ".
            tail = data.rsplit(") ", 1)[1].split()
            return tail[19]
        except (IndexError, ValueError):
            return None

    @staticmethod
    def _process_group(pid: int) -> int | None:
        if os.name == "nt":
            return None
        try:
            return os.getpgid(pid)
        except ProcessLookupError:
            return None

    @staticmethod
    def _terminate_process_group(pid: int, sig: signal.Signals) -> None:
        if os.name == "nt":
            return
        try:
            os.killpg(pid, sig)
        except (ProcessLookupError, PermissionError):
            return

    @staticmethod
    def _execution_env(
        *,
        execution_id: str | None,
        run_token: str | None,
        dispatch_id: str | None,
        host_id: str | None,
        session_name: str | None,
    ) -> dict[str, str] | None:
        if not execution_id or not run_token:
            return None
        env = os.environ.copy()
        env.update(
            {
                "COOAGENTS_OWNER": "cooagents",
                "COOAGENTS_EXECUTION_ID": execution_id,
                "COOAGENTS_RUN_TOKEN": run_token,
            }
        )
        if dispatch_id:
            env["COOAGENTS_DISPATCH_ID"] = dispatch_id
        if host_id:
            env["COOAGENTS_HOST_ID"] = host_id
        if session_name:
            env["COOAGENTS_SESSION_NAME"] = session_name
        return env

    async def _run_local(
        self,
        cmd: list[str],
        cwd: str,
        *,
        timeout: float | None = 30.0,
        execution_id: str | None = None,
        run_token: str | None = None,
        dispatch_id: str | None = None,
        host_id: str | None = None,
        session_name: str | None = None,
    ) -> tuple[str, str, int]:
        """Run ``cmd`` locally; return ``(stdout, stderr, returncode)``.

        Mirrors :meth:`AcpxExecutor._run_local` but also captures stderr so
        :class:`SessionLifecycleError` can attach a tail.

        Phase 11: ``timeout`` (seconds) bounds wall-clock time. On timeout
        the subprocess (and any session-leader children) get SIGKILL and
        :class:`TimeoutError` is raised. ``None`` keeps the legacy unbounded
        behavior for callers that genuinely need it.

        ``start_new_session=True`` puts the child in its own process group
        so a daemonized grandchild (e.g. codex-acp) cannot hold cooagents'
        stdout/stderr pipe open after ``proc.kill()``.

        Phase 11.2: on uvloop, ``proc.communicate()`` can still hang forever
        after the direct ``acpx`` child exits 0 and flushes its JSON result if
        a detached descendant keeps the pipe fd open. Pump stdout/stderr in
        background tasks and wait on ``proc.wait()`` instead of EOF.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=self._execution_env(
                execution_id=execution_id,
                run_token=run_token,
                dispatch_id=dispatch_id,
                host_id=host_id,
                session_name=session_name,
            ),
        )
        if execution_id and self._agent_execution_repo is not None:
            try:
                await self._agent_execution_repo.mark_process_started(
                    execution_id,
                    pid=proc.pid,
                    pgid=self._process_group(proc.pid),
                    pid_starttime=self._pid_starttime(proc.pid),
                    cwd=cwd,
                )
            except Exception:
                logger.exception(
                    "agent execution %s process-start record failed",
                    execution_id,
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
                # Expected when the direct child exits but a detached
                # descendant keeps the pipe open. Preserve bytes collected so
                # far and let the caller continue.
                return

        stdout_task = asyncio.create_task(_pump_stream(proc.stdout, stdout_chunks))
        stderr_task = asyncio.create_task(_pump_stream(proc.stderr, stderr_chunks))
        try:
            await self._wait_for_direct_exit(proc, timeout)
            # Let the pump tasks drain bytes that arrived before process exit
            # before we cancel them. This is especially important for tests
            # and for uvloop, where exit notification can beat the reader task.
            await asyncio.sleep(0)
        except asyncio.TimeoutError:
            # Phase 11.1: ``proc.kill()`` raises ``ProcessLookupError`` when
            # the subprocess already exited (acpx 0.6.x exits cleanly while
            # leaving codex-acp grandchild holding the pipe — exact shape
            # Phase 10 hit). Swallow it; the kill is best-effort cleanup.
            self._terminate_process_group(proc.pid, signal.SIGTERM)
            try:
                proc.kill()
            except ProcessLookupError:
                pass
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except (asyncio.TimeoutError, ProcessLookupError):
                self._terminate_process_group(proc.pid, signal.SIGKILL)
                pass
            raise TimeoutError(
                f"acpx subprocess timed out after {timeout}s; "
                f"cmd[0..2]={cmd[:3]!r}"
            )
        finally:
            for task in (stdout_task, stderr_task):
                task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            if execution_id and self._agent_execution_repo is not None:
                try:
                    await self._agent_execution_repo.mark_exited(
                        execution_id, exit_code=proc.returncode,
                    )
                except Exception:
                    logger.exception(
                        "agent execution %s exit record failed",
                        execution_id,
                    )
        return (
            b"".join(stdout_chunks).decode("utf-8", errors="replace").strip(),
            b"".join(stderr_chunks).decode("utf-8", errors="replace").strip(),
            proc.returncode,
        )

    async def _wait_for_direct_exit(
        self, proc: asyncio.subprocess.Process, timeout: float | None,
    ) -> int:
        """Return when the direct child exits, even if descendants hold pipes."""
        wait_task = asyncio.create_task(proc.wait())
        loop = asyncio.get_running_loop()
        deadline = None if timeout is None else loop.time() + timeout
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

    # ---- one-shot --------------------------------------------------------

    async def run_oneshot(
        self,
        agent: str,
        worktree: str,
        timeout_sec: int,
        task_file: str | None = None,
        prompt: str | None = None,
        *,
        host_id: str = "local",
        workspace_id: str | None = None,
        correlation_id: str | None = None,
        execution_id: str | None = None,
        run_token: str | None = None,
        session_name: str | None = None,
    ) -> tuple[str, int]:
        """Delegate to :meth:`AcpxExecutor.run_once` byte-for-byte."""
        execution_kwargs: dict[str, str] = {}
        if execution_id:
            execution_kwargs["execution_id"] = execution_id
        if run_token:
            execution_kwargs["run_token"] = run_token
        if session_name:
            execution_kwargs["session_name"] = session_name
        return await self._executor.run_once(
            agent, worktree, timeout_sec,
            task_file=task_file, prompt=prompt,
            host_id=host_id, workspace_id=workspace_id, correlation_id=correlation_id,
            **execution_kwargs,
        )

    # ---- one-shot with progress (Phase 3) -------------------------------

    async def run_with_progress(
        self,
        *,
        cmd: list[str],
        cwd: str,
        heartbeat: HeartbeatCallback,
        heartbeat_interval_s: float,
        idle_timeout_s: float,
        step_tag: str,
        execution_id: str | None = None,
        run_token: str | None = None,
        dispatch_id: str | None = None,
        host_id: str | None = None,
        session_name: str | None = None,
        advance_probe: AdvanceProbe | None = None,
    ) -> tuple[str, int, list[ProgressTick]]:
        """Spawn ``cmd`` and call ``heartbeat`` on every interval tick.

        Returns ``(stdout, returncode, progress_log)``.

        Raises :class:`IdleTimeoutError` when no heartbeat advances within
        ``idle_timeout_s``: the subprocess is killed and the exception
        propagates so the caller can branch on it (Phase 3 SM maps it to
        ``dispatch_state="timeout"``).

        ``heartbeat`` is awaited inside its own try/except so a stuck
        callback (slow DB UPDATE, blocked event log) cannot kill the LLM
        call — the tick is logged at exception level and the loop carries
        on. In Phase 3 oneshot mode the "advance" predicate is
        process-alive; Phase 4 will swap it for a status_session-derived
        signal inside the SM-level closure.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
            env=self._execution_env(
                execution_id=execution_id,
                run_token=run_token,
                dispatch_id=dispatch_id,
                host_id=host_id,
                session_name=session_name,
            ),
        )
        if execution_id and self._agent_execution_repo is not None:
            try:
                await self._agent_execution_repo.mark_process_started(
                    execution_id,
                    pid=proc.pid,
                    pgid=self._process_group(proc.pid),
                    pid_starttime=self._pid_starttime(proc.pid),
                    cwd=cwd,
                )
            except Exception:
                logger.exception(
                    "agent execution %s process-start record failed",
                    execution_id,
                )
        start = self._monotonic()
        progress_log: list[ProgressTick] = []
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
                return

        async def ticker() -> None:
            last_advance = start
            while True:
                await asyncio.sleep(heartbeat_interval_s)
                now = self._monotonic()
                tick = ProgressTick(
                    ts=self._clock(), elapsed_s=int(now - start),
                )
                progress_log.append(tick)
                try:
                    await heartbeat(tick)
                except Exception:
                    logger.exception(
                        "llm_runner: heartbeat callback raised at step=%r",
                        step_tag,
                    )
                if execution_id and self._agent_execution_repo is not None:
                    try:
                        await self._agent_execution_repo.heartbeat(execution_id)
                    except Exception:
                        logger.warning(
                            "agent execution %s heartbeat failed",
                            execution_id,
                            exc_info=True,
                        )
                if advance_probe is None:
                    # Phase 3 oneshot: process-alive == advance.
                    advanced = proc.returncode is None
                else:
                    try:
                        advanced = await advance_probe()
                    except Exception:
                        advanced = False
                        logger.warning(
                            "llm_runner: advance probe failed at step=%r",
                            step_tag,
                            exc_info=True,
                        )
                if advanced:
                    last_advance = now
                if (now - last_advance) >= idle_timeout_s:
                    logger.warning(
                        "llm_runner: idle_timeout step=%r idle_window_s=%s",
                        step_tag, idle_timeout_s,
                    )
                    self._terminate_process_group(proc.pid, signal.SIGTERM)
                    try:
                        proc.kill()
                    except ProcessLookupError:
                        pass
                    raise IdleTimeoutError(
                        step_tag=step_tag,
                        idle_window_s=int(idle_timeout_s),
                    )

        stdout_task = asyncio.create_task(_pump_stream(proc.stdout, stdout_chunks))
        stderr_task = asyncio.create_task(_pump_stream(proc.stderr, stderr_chunks))
        ticker_task = asyncio.create_task(ticker())
        idle_exc: IdleTimeoutError | None = None
        try:
            await proc.wait()
            await asyncio.sleep(0)
        finally:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass
            except IdleTimeoutError as exc:
                idle_exc = exc
            for task in (stdout_task, stderr_task):
                task.cancel()
            await asyncio.gather(stdout_task, stderr_task, return_exceptions=True)
            if execution_id and self._agent_execution_repo is not None:
                try:
                    await self._agent_execution_repo.mark_exited(
                        execution_id, exit_code=proc.returncode,
                    )
                except Exception:
                    logger.exception(
                        "agent execution %s exit record failed",
                        execution_id,
                    )
        if idle_exc is not None:
            raise idle_exc
        return (
            b"".join(stdout_chunks).decode("utf-8", errors="replace").strip(),
            proc.returncode if proc.returncode is not None else -1,
            progress_log,
        )

    # ---- session lifecycle ----------------------------------------------

    async def start_session(
        self,
        *,
        name: str,
        anchor_cwd: str,
        agent: str,
        execution_id: str | None = None,
        run_token: str | None = None,
        dispatch_id: str | None = None,
        host_id: str | None = None,
    ) -> Session:
        """``acpx --cwd <anchor> <agent> sessions ensure --name <name>``.

        Raises :class:`SessionLifecycleError` if rc != 0.
        """
        resolved = self._resolve_agent(agent)
        cmd = self._build_ensure_cmd(name, anchor_cwd, resolved)
        _stdout, stderr, rc = await self._run_local(
            cmd,
            anchor_cwd,
            timeout=30.0,
            execution_id=execution_id,
            run_token=run_token,
            dispatch_id=dispatch_id,
            host_id=host_id,
            session_name=name,
        )
        if rc != 0:
            raise SessionLifecycleError("ensure", rc, stderr[-512:])
        session_mode = getattr(self._acpx_cfg(), "session_mode", None)
        if session_mode and resolved == "codex":
            mode_cmd = self._build_set_mode_cmd(
                name, anchor_cwd, resolved, session_mode,
            )
            _stdout, stderr, rc = await self._run_local(
                mode_cmd,
                anchor_cwd,
                timeout=30.0,
                execution_id=execution_id,
                run_token=run_token,
                dispatch_id=dispatch_id,
                host_id=host_id,
                session_name=name,
            )
            if rc != 0:
                raise SessionLifecycleError("set-mode", rc, stderr[-512:])
        return Session(
            name=name,
            anchor_cwd=anchor_cwd,
            agent=resolved,
            created_at=self._clock(),
        )

    async def prompt_session(
        self,
        session: Session,
        *,
        text: str | None = None,
        task_file: str | None = None,
        timeout_sec: int,
        execution_id: str | None = None,
        run_token: str | None = None,
        dispatch_id: str | None = None,
        host_id: str | None = None,
    ) -> tuple[str, int]:
        """Run ``prompt --session`` from the session's anchor cwd."""
        cmd = self._build_prompt_cmd(
            session, text=text, task_file=task_file, timeout_sec=timeout_sec,
        )
        stdout, _stderr, rc = await self._run_local(
            cmd,
            session.anchor_cwd,
            timeout=30.0,
            execution_id=execution_id,
            run_token=run_token,
            dispatch_id=dispatch_id,
            host_id=host_id,
            session_name=session.name,
        )
        return stdout, rc

    async def prompt_session_with_progress(
        self,
        session: Session,
        *,
        task_file: str | None = None,
        text: str | None = None,
        timeout_sec: int,
        heartbeat: HeartbeatCallback,
        heartbeat_interval_s: float,
        idle_timeout_s: float,
        step_tag: str,
        execution_id: str | None = None,
        run_token: str | None = None,
        dispatch_id: str | None = None,
        host_id: str | None = None,
    ) -> tuple[str, int, list[ProgressTick]]:
        """Run ``prompt --session`` with the same heartbeat machinery as
        :meth:`run_with_progress`.

        Phase 9: the SM-level wrapper that turns "session-mode dispatch"
        into a drop-in replacement for the oneshot heartbeat path.
        Builds the same ``prompt --session`` cmd as :meth:`prompt_session`,
        but intentionally omits ``acpx --timeout``. DevWork session turns can
        keep writing after the acpx transport times out; completion must come
        from the prompt command naturally finishing, while idle detection uses
        session-record progress instead of direct process liveness.
        """
        cmd = self._build_prompt_cmd(
            session, text=text, task_file=task_file, timeout_sec=None,
        )
        execution_kwargs: dict[str, str] = {}
        if execution_id:
            execution_kwargs["execution_id"] = execution_id
        if run_token:
            execution_kwargs["run_token"] = run_token
        if dispatch_id:
            execution_kwargs["dispatch_id"] = dispatch_id
        if host_id:
            execution_kwargs["host_id"] = host_id
        if execution_kwargs:
            execution_kwargs["session_name"] = session.name
        last_activity = await self._session_activity_token(session)

        async def advance_probe() -> bool:
            nonlocal last_activity
            current = await self._session_activity_token(session)
            if current is None:
                return False
            if current != last_activity:
                last_activity = current
                return True
            return False

        return await self.run_with_progress(
            cmd=cmd,
            cwd=session.anchor_cwd,
            heartbeat=heartbeat,
            heartbeat_interval_s=heartbeat_interval_s,
            idle_timeout_s=idle_timeout_s,
            step_tag=step_tag,
            advance_probe=advance_probe,
            **execution_kwargs,
        )

    async def status_session(self, session: Session) -> dict[str, Any]:
        """Parse ``status --session`` JSON or ``key: value`` lines.

        Returns ``{}`` if rc != 0. ``{"session": "-", "status": "no-session"}``
        for an unknown session is rc=0 and is returned as-is (spike Q(b)).
        """
        cmd = self._build_status_cmd(session)
        stdout, _stderr, rc = await self._run_local(
            cmd, session.anchor_cwd, timeout=30.0,
        )
        if rc != 0:
            return {}
        try:
            parsed_json = json.loads(stdout)
        except json.JSONDecodeError:
            parsed_json = None
        if isinstance(parsed_json, dict):
            return parsed_json
        parsed: dict[str, str] = {}
        for line in stdout.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            parsed[key.strip()] = value.strip()
        return parsed

    async def _session_record(self, session: Session) -> dict[str, Any] | None:
        cmd = self._build_list_cmd(session.agent, session.anchor_cwd)
        stdout, _stderr, rc = await self._run_local(
            cmd, session.anchor_cwd, timeout=30.0,
        )
        if rc != 0:
            return None
        try:
            payload = json.loads(stdout)
        except json.JSONDecodeError:
            return None
        if not isinstance(payload, list):
            return None
        for item in payload:
            if not isinstance(item, dict):
                continue
            if item.get("name") != session.name:
                continue
            if item.get("cwd") not in (None, session.anchor_cwd):
                continue
            return item
        return None

    async def _session_activity_token(
        self, session: Session,
    ) -> tuple[Any, ...] | None:
        record = await self._session_record(session)
        if record is None:
            return None
        messages = record.get("messages")
        message_count = len(messages) if isinstance(messages, list) else None
        event_log = record.get("eventLog")
        if not isinstance(event_log, dict):
            event_log = {}
        return (
            record.get("lastSeq") or record.get("last_seq"),
            record.get("lastUsedAt") or record.get("last_used_at"),
            record.get("updated_at"),
            record.get("lastPromptAt") or record.get("last_prompt_at"),
            record.get("lastAgentExitAt") or record.get("last_agent_exit_at"),
            record.get("lastAgentExitCode")
            if "lastAgentExitCode" in record
            else record.get("last_agent_exit_code"),
            record.get("lastAgentExitSignal")
            if "lastAgentExitSignal" in record
            else record.get("last_agent_exit_signal"),
            record.get("closed"),
            message_count,
            event_log.get("last_write_error"),
        )

    async def cancel_session(self, session: Session) -> None:
        """Best-effort cancel; logs warning on rc != 0 but does not raise.

        Cancel of an already-stopped session is a no-op success in acpx.
        """
        cmd = self._build_cancel_cmd(session)
        _stdout, stderr, rc = await self._run_local(
            cmd, session.anchor_cwd, timeout=30.0,
        )
        if rc != 0:
            logger.warning(
                "llm_runner: cancel session %r at cwd=%r rc=%d stderr=%r",
                session.name, session.anchor_cwd, rc, stderr[-256:],
            )

    async def delete_session(self, session: Session) -> None:
        """Two-step destroy: cancel (best-effort) → close.

        Raises :class:`SessionLifecycleError` if ``close`` fails for any
        reason other than ``no named session`` (already-closed / unknown).

        Phase 11: acpx 0.6.x has no ``sessions prune`` subcommand. ``close``
        is the entire teardown story; the next boot's
        :meth:`orphan_sweep_at_boot` covers anything missed.
        """
        await self.cancel_session(session)
        cmd = self._build_close_cmd(session)
        _stdout, stderr, rc = await self._run_local(
            cmd, session.anchor_cwd, timeout=30.0,
        )
        if rc != 0:
            if "no named session" not in stderr.lower():
                raise SessionLifecycleError("close", rc, stderr[-512:])

    # ---- fleet ops -------------------------------------------------------

    async def orphan_sweep_at_boot(
        self, *, name_prefixes: tuple[str, ...],
    ) -> list[Session]:
        """Boot-time reaper: list sessions per-agent, delete those matching.

        Best-effort: per-session failures are logged and the sweep
        continues. Returns the list of sessions it attempted to clean.
        """
        cleaned: list[Session] = []
        sweep_cwd = str(getattr(self._executor, "project_root", "."))
        for agent in _SWEEP_AGENTS:
            cmd = self._build_list_cmd(agent, sweep_cwd)
            stdout, stderr, rc = await self._run_local(
                cmd, sweep_cwd, timeout=60.0,
            )
            if rc != 0:
                logger.warning(
                    "orphan_sweep: list rc=%d for agent=%s stderr=%r",
                    rc, agent, stderr[-256:],
                )
                continue
            try:
                entries = json.loads(stdout) if stdout else []
            except json.JSONDecodeError:
                logger.warning(
                    "orphan_sweep: malformed sessions list JSON for agent=%s",
                    agent,
                )
                continue
            if not isinstance(entries, list):
                continue
            for entry in entries:
                if not isinstance(entry, dict):
                    continue
                name = entry.get("name") or ""
                if not any(name.startswith(p) for p in name_prefixes):
                    continue
                if entry.get("closed"):
                    continue
                anchor = entry.get("cwd") or ""
                if not anchor:
                    continue
                s = Session(
                    name=name,
                    anchor_cwd=anchor,
                    agent=agent,
                    created_at=entry.get("createdAt") or "",
                )
                try:
                    await self.delete_session(s)
                    cleaned.append(s)
                except Exception:
                    logger.exception(
                        "orphan_sweep: delete_session failed for %s", name,
                    )
        return cleaned
