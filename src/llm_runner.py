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
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any, Callable

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
    ) -> None:
        self._executor = executor
        self._config = config
        self._clock = clock or _default_clock
        # asyncio.create_task only weakly refs tasks; without a strong ref the
        # GC can drop a deferred prune mid-run. Keep them alive here.
        self._pending_tasks: set[asyncio.Task] = set()

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
        return [
            *self._common_flags(session.anchor_cwd),
            session.agent, "sessions", "close", "--name", session.name,
        ]

    def _build_prune_cmd(self, agent: str, before_iso: str, anchor_cwd: str) -> list[str]:
        # spike Q(c) Implication: --older-than 0 is invalid; --before <iso> works.
        # PRD architecture-notes line 215: --include-history clears closed-session bookkeeping too.
        return [
            *self._common_flags(anchor_cwd),
            agent, "sessions", "prune",
            "--before", before_iso, "--include-history",
        ]

    def _build_list_cmd(self, agent: str, cwd: str) -> list[str]:
        return [*self._common_flags(cwd), agent, "sessions", "list"]

    # ---- subprocess plumbing --------------------------------------------

    async def _run_local(
        self, cmd: list[str], cwd: str
    ) -> tuple[str, str, int]:
        """Run ``cmd`` locally; return ``(stdout, stderr, returncode)``.

        Mirrors :meth:`AcpxExecutor._run_local` but also captures stderr so
        :class:`SessionLifecycleError` can attach a tail.
        """
        proc = await asyncio.create_subprocess_exec(
            *cmd,
            cwd=cwd,
            stdin=asyncio.subprocess.DEVNULL,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
        )
        stdout, stderr = await proc.communicate()
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

    # ---- session lifecycle ----------------------------------------------

    async def start_session(
        self, *, name: str, anchor_cwd: str, agent: str,
    ) -> Session:
        """``acpx --cwd <anchor> <agent> sessions ensure --name <name>``.

        Raises :class:`SessionLifecycleError` if rc != 0.
        """
        resolved = self._resolve_agent(agent)
        cmd = self._build_ensure_cmd(name, anchor_cwd, resolved)
        _stdout, stderr, rc = await self._run_local(cmd, anchor_cwd)
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
        stdout, _stderr, rc = await self._run_local(cmd, session.anchor_cwd)
        return stdout, rc

    async def status_session(self, session: Session) -> dict[str, str]:
        """Parse ``status --session`` ``key: value`` lines into a dict.

        Returns ``{}`` if rc != 0. ``{"session": "-", "status": "no-session"}``
        for an unknown session is rc=0 and is returned as-is (spike Q(b)).
        """
        cmd = self._build_status_cmd(session)
        stdout, _stderr, rc = await self._run_local(cmd, session.anchor_cwd)
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
        _stdout, stderr, rc = await self._run_local(cmd, session.anchor_cwd)
        if rc != 0:
            logger.warning(
                "llm_runner: cancel session %r at cwd=%r rc=%d stderr=%r",
                session.name, session.anchor_cwd, rc, stderr[-256:],
            )

    async def delete_session(self, session: Session) -> None:
        """Two-step destroy: cancel (best-effort) → close → deferred prune.

        Raises :class:`SessionLifecycleError` if ``close`` fails for any
        reason other than ``no named session`` (already-closed / unknown).
        """
        await self.cancel_session(session)
        cmd = self._build_close_cmd(session)
        _stdout, stderr, rc = await self._run_local(cmd, session.anchor_cwd)
        if rc != 0:
            if "no named session" not in stderr.lower():
                raise SessionLifecycleError("close", rc, stderr[-512:])
            # Session was already gone — nothing to prune.
            return
        # Schedule a deferred prune so closed-session bookkeeping is cleared.
        before = (
            datetime.now(timezone.utc) + timedelta(seconds=1)
        ).isoformat()
        task = asyncio.create_task(
            self._deferred_prune(session.agent, before, session.anchor_cwd)
        )
        if task is not None:  # tests monkeypatch create_task to return None
            self._pending_tasks.add(task)
            task.add_done_callback(self._pending_tasks.discard)

    async def _deferred_prune(self, agent: str, before_iso: str, anchor_cwd: str) -> None:
        """Background prune; failures are logged but never raised.

        The next boot's :meth:`orphan_sweep_at_boot` covers anything missed.
        """
        try:
            cmd = self._build_prune_cmd(agent, before_iso, anchor_cwd)
            _stdout, stderr, rc = await self._run_local(cmd, anchor_cwd)
            if rc != 0:
                logger.warning(
                    "llm_runner: deferred prune rc=%d stderr=%r",
                    rc, stderr[-256:],
                )
        except Exception:
            logger.exception("llm_runner: deferred prune raised; ignoring")

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
            stdout, stderr, rc = await self._run_local(cmd, sweep_cwd)
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
