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
    ) -> None:
        self._executor = executor
        self._config = config
        self._clock = clock or _default_clock
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

    def _build_prompt_cmd(
        self,
        session: Session,
        *,
        text: str | None,
        task_file: str | None,
        timeout_sec: int,
    ) -> list[str]:
        assert (text is None) != (task_file is None), (
            "prompt_session requires exactly one of text or task_file"
        )
        cmd = self._common_flags(session.anchor_cwd) + [
            "--timeout", str(timeout_sec),
            session.agent, "prompt", "--session", session.name,
        ]
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

    async def _run_local(
        self, cmd: list[str], cwd: str, *, timeout: float | None = 30.0,
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
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            start_new_session=True,
        )
        try:
            if timeout is None:
                stdout, stderr = await proc.communicate()
            else:
                stdout, stderr = await asyncio.wait_for(
                    proc.communicate(), timeout=timeout,
                )
        except asyncio.TimeoutError:
            proc.kill()
            try:
                await asyncio.wait_for(proc.wait(), timeout=2.0)
            except asyncio.TimeoutError:
                pass
            raise TimeoutError(
                f"acpx subprocess timed out after {timeout}s; "
                f"cmd[0..2]={cmd[:3]!r}"
            )
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            stderr.decode("utf-8", errors="replace").strip(),
            proc.returncode,
        )

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
    ) -> tuple[str, int]:
        """Delegate to :meth:`AcpxExecutor.run_once` byte-for-byte."""
        return await self._executor.run_once(
            agent, worktree, timeout_sec,
            task_file=task_file, prompt=prompt,
            host_id=host_id, workspace_id=workspace_id, correlation_id=correlation_id,
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
        )
        start = self._monotonic()
        progress_log: list[ProgressTick] = []

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
                # Phase 3 oneshot: process-alive == advance. Phase 4 swaps
                # this for a status_session-derived predicate.
                if proc.returncode is None:
                    last_advance = now
                if (now - last_advance) >= idle_timeout_s:
                    logger.warning(
                        "llm_runner: idle_timeout step=%r idle_window_s=%s",
                        step_tag, idle_timeout_s,
                    )
                    proc.kill()
                    raise IdleTimeoutError(
                        step_tag=step_tag,
                        idle_window_s=int(idle_timeout_s),
                    )

        ticker_task = asyncio.create_task(ticker())
        idle_exc: IdleTimeoutError | None = None
        try:
            stdout, _stderr = await proc.communicate()
        finally:
            ticker_task.cancel()
            try:
                await ticker_task
            except asyncio.CancelledError:
                pass
            except IdleTimeoutError as exc:
                idle_exc = exc
        if idle_exc is not None:
            raise idle_exc
        return (
            stdout.decode("utf-8", errors="replace").strip(),
            proc.returncode if proc.returncode is not None else -1,
            progress_log,
        )

    # ---- session lifecycle ----------------------------------------------

    async def start_session(
        self, *, name: str, anchor_cwd: str, agent: str,
    ) -> Session:
        """``acpx --cwd <anchor> <agent> sessions ensure --name <name>``.

        Raises :class:`SessionLifecycleError` if rc != 0.
        """
        resolved = self._resolve_agent(agent)
        cmd = self._build_ensure_cmd(name, anchor_cwd, resolved)
        _stdout, stderr, rc = await self._run_local(
            cmd, anchor_cwd, timeout=30.0,
        )
        if rc != 0:
            raise SessionLifecycleError("ensure", rc, stderr[-512:])
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
    ) -> tuple[str, int]:
        """Run ``prompt --session`` from the session's anchor cwd."""
        cmd = self._build_prompt_cmd(
            session, text=text, task_file=task_file, timeout_sec=timeout_sec,
        )
        stdout, _stderr, rc = await self._run_local(
            cmd, session.anchor_cwd, timeout=30.0,
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
    ) -> tuple[str, int, list[ProgressTick]]:
        """Run ``prompt --session`` with the same heartbeat machinery as
        :meth:`run_with_progress`.

        Phase 9: the SM-level wrapper that turns "session-mode dispatch"
        into a drop-in replacement for the oneshot heartbeat path.
        Builds the same ``prompt --session`` cmd as :meth:`prompt_session`
        then delegates to :meth:`run_with_progress` so heartbeat /
        idle-timeout semantics stay identical to oneshot.
        """
        cmd = self._build_prompt_cmd(
            session, text=text, task_file=task_file, timeout_sec=timeout_sec,
        )
        return await self.run_with_progress(
            cmd=cmd,
            cwd=session.anchor_cwd,
            heartbeat=heartbeat,
            heartbeat_interval_s=heartbeat_interval_s,
            idle_timeout_s=idle_timeout_s,
            step_tag=step_tag,
        )

    async def status_session(self, session: Session) -> dict[str, str]:
        """Parse ``status --session`` ``key: value`` lines into a dict.

        Returns ``{}`` if rc != 0. ``{"session": "-", "status": "no-session"}``
        for an unknown session is rc=0 and is returned as-is (spike Q(b)).
        """
        cmd = self._build_status_cmd(session)
        stdout, _stderr, rc = await self._run_local(
            cmd, session.anchor_cwd, timeout=30.0,
        )
        if rc != 0:
            return {}
        parsed: dict[str, str] = {}
        for line in stdout.splitlines():
            if ":" not in line:
                continue
            key, _, value = line.partition(":")
            parsed[key.strip()] = value.strip()
        return parsed

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
