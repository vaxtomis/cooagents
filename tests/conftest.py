"""Shared test fixtures.

Provides ``FakeOSSStore`` — an in-memory duck-type of ``OSSFileStore`` used
by registry / OSS-aware tests. Avoids hitting a real OSS bucket from unit
tests; real OSS stays covered by the integration suite under
``tests/integration/``.
"""
from __future__ import annotations

from time import time_ns

import pytest

from src.exceptions import NotFoundError
from src.storage.base import FileRef


class FakeOSSStore:
    """In-memory duck-type of ``OSSFileStore`` for registry tests.

    Matches the OSSFileStore contract surface in use:
      * ``put_bytes``, ``get_bytes``, ``stat``, ``delete``, ``list``
      * ``close()`` (idempotent)
    """

    def __init__(self) -> None:
        self._objects: dict[str, tuple[bytes, str]] = {}
        self._etag_counter = 0
        self._head_calls = 0
        self._get_calls = 0

    def _next_etag(self) -> str:
        self._etag_counter += 1
        return f"etag{self._etag_counter:032x}"

    async def put_bytes(self, key: str, data: bytes) -> FileRef:
        etag = self._next_etag()
        self._objects[key] = (data, etag)
        return FileRef(key=key, size=len(data), mtime_ns=time_ns(), etag=etag)

    async def get_bytes(self, key: str) -> bytes:
        self._get_calls += 1
        if key not in self._objects:
            raise NotFoundError(f"key not found: {key!r}")
        return self._objects[key][0]

    async def stat(self, key: str) -> FileRef | None:
        self._head_calls += 1
        if key not in self._objects:
            return None
        data, etag = self._objects[key]
        return FileRef(key=key, size=len(data), mtime_ns=0, etag=etag)

    async def delete(self, key: str) -> None:
        self._objects.pop(key, None)

    async def list(self, prefix: str) -> list[FileRef]:
        refs: list[FileRef] = []
        for k, (d, etag) in self._objects.items():
            if prefix == "" or k.startswith(prefix):
                refs.append(
                    FileRef(key=k, size=len(d), mtime_ns=0, etag=etag)
                )
        return sorted(refs, key=lambda r: r.key)

    async def close(self) -> None:
        self._objects.clear()


@pytest.fixture
def fake_oss_store() -> FakeOSSStore:
    return FakeOSSStore()


# --------------------------------------------------------------------------
# DevWork SM: LLMRunner injection (Phase 2)
#
# DevWorkStateMachine now requires an ``llm_runner=`` kwarg. Tests that
# previously injected a ``ScriptedExecutor`` keep working because
# :meth:`LLMRunner.run_oneshot` is a 1-line delegation to
# ``executor.run_once`` — wrapping the existing scripted executor in a
# real LLMRunner preserves every assertion against ``executor.calls`` while
# exercising the production delegation seam.
#
# ``fake_llm_runner`` is the lightweight stub for tests that only need to
# observe call shape (no scripted side-effects).
# --------------------------------------------------------------------------


def make_test_llm_runner(executor):
    """Wrap a scripted/fake executor in a real LLMRunner instance.

    Used by every DevWorkStateMachine fixture site after Phase 2 made
    ``llm_runner=`` required.

    Phase 3: tests script behavior via ``executor.run_once`` (the
    ScriptedExecutor pattern). The production ``run_with_progress`` would
    instead spawn a real acpx subprocess, which the test environment has
    no binary for. The subclass below intercepts ``run_with_progress`` and
    delegates to the scripted ``run_once`` so existing scripted tests keep
    driving the state machine without changes. Heartbeat callbacks fire
    zero times (matches the empty ``progress_log`` semantics — tests that
    care about ticks use ``_FakeLLMRunner`` directly).
    """
    from src.llm_runner import LLMRunner, Session

    class _TestLLMRunner(LLMRunner):
        def __init__(self, *args, **kwargs):
            super().__init__(*args, **kwargs)
            # Phase 9: per-instance trackers for session lifecycle assertions.
            # Tests read these to verify session names and ordering without
            # spawning a real acpx subprocess.
            self.created_sessions: list[str] = []
            self.deleted_sessions: list[str] = []
            # Phase 10: counters for the acceptance harness. Increment
            # before the delegate call so a raising executor still bumps
            # the count (matches PRD intent — count acpx invocations
            # regardless of success).
            self.prompt_call_count: int = 0
            self.oneshot_call_count: int = 0

        def _resolve_agent(self, agent_type):
            # Phase 9: ScriptedExecutor / FakeOSSStore-shaped fixtures may
            # not implement ``_resolve_agent``. The production passthrough
            # in :class:`AcpxExecutor` is identity-ish ("claude" -> "claude",
            # everything else -> "codex"); mirror that here so tests can
            # inject any executor without a no-op shim.
            return "claude" if agent_type == "claude" else "codex"

        async def run_with_progress(
            self, *, cmd, cwd, heartbeat, heartbeat_interval_s,
            idle_timeout_s, step_tag, **_execution_kwargs,
        ):
            self.oneshot_call_count += 1   # Phase 10
            # Parse by sentinel rather than positional index so the test
            # adapter survives flag additions to ``_build_acpx_exec_cmd``
            # (e.g. --model, --json-strict). The agent token is the one
            # immediately preceding "exec"; --timeout / --file / --prompt
            # are scanned by name.
            try:
                exec_idx = cmd.index("exec")
                agent = cmd[exec_idx - 1]
            except (ValueError, IndexError):
                agent = "claude"
            timeout_sec = 0
            if "--timeout" in cmd:
                try:
                    timeout_sec = int(cmd[cmd.index("--timeout") + 1])
                except (ValueError, IndexError):
                    timeout_sec = 0
            task_file = None
            prompt = None
            if "--file" in cmd:
                task_file = cmd[cmd.index("--file") + 1]
            if "--prompt" in cmd:
                prompt = cmd[cmd.index("--prompt") + 1]
            stdout, rc = await self._executor.run_once(
                agent, cwd, timeout_sec,
                task_file=task_file, prompt=prompt,
            )
            return stdout, rc, []

        async def prompt_session_with_progress(
            self, session, *, task_file=None, text=None, timeout_sec,
            heartbeat, heartbeat_interval_s, idle_timeout_s, step_tag,
            **_execution_kwargs,
        ):
            self.prompt_call_count += 1   # Phase 10
            # Phase 9: session-mode in test mode delegates to the same
            # scripted ``run_once`` as oneshot. The scripted Step2/3/4/5
            # actions parse the prompt body to find their output paths,
            # not the cwd, so anchoring at ``session.anchor_cwd`` (the
            # devworks dir) keeps every existing scripted helper working.
            stdout, rc = await self._executor.run_once(
                session.agent, session.anchor_cwd, timeout_sec,
                task_file=task_file, prompt=text,
            )
            return stdout, rc, []

        async def start_session(self, *, name, anchor_cwd, agent, **_execution_kwargs):
            self.created_sessions.append(name)
            return Session(
                name=name, anchor_cwd=anchor_cwd, agent=agent,
                created_at="t",
            )

        async def delete_session(self, session):
            self.deleted_sessions.append(session.name)

    return _TestLLMRunner(executor=executor)


class _FakeLLMRunner:
    """Minimal LLMRunner stub for tests that only assert call shape.

    Tests can read ``.calls`` (each entry is a kwargs dict) and tweak
    ``.run_oneshot_return`` to control the (stdout, rc) tuple.
    """

    def __init__(self) -> None:
        self.calls: list[dict] = []
        self.run_oneshot_return: tuple[str, int] = ("", 0)
        # Phase 3: when set to a list of ProgressTick (or any object with
        # ``ts`` and ``elapsed_s`` attributes — simple namedtuples work),
        # ``run_with_progress`` will await ``heartbeat(tick)`` for each
        # entry before returning ``run_oneshot_return``. Default empty so
        # existing tests that exercise the SM see zero heartbeats.
        self.progress_ticks: list = []
        # Phase 3: opt-in — set to an IdleTimeoutError instance to make the
        # next ``run_with_progress`` call raise after emitting all queued
        # ticks. Cleared back to None automatically after the raise so a
        # single fixture can drive a single failure case.
        self.next_idle_timeout: Exception | None = None

    async def run_oneshot(
        self, agent, worktree, timeout_sec,
        task_file=None, prompt=None, *,
        host_id="local", workspace_id=None, correlation_id=None,
    ):
        self.calls.append(dict(
            agent=agent, worktree=worktree, timeout_sec=timeout_sec,
            task_file=task_file, prompt=prompt, host_id=host_id,
            workspace_id=workspace_id, correlation_id=correlation_id,
        ))
        return self.run_oneshot_return

    async def run_with_progress(
        self, *, cmd, cwd, heartbeat, heartbeat_interval_s,
        idle_timeout_s, step_tag, **_execution_kwargs,
    ):
        self.calls.append(dict(
            kind="progress", cmd=list(cmd), cwd=cwd, step_tag=step_tag,
            heartbeat_interval_s=heartbeat_interval_s,
            idle_timeout_s=idle_timeout_s,
        ))
        for tick in self.progress_ticks:
            await heartbeat(tick)
        if self.next_idle_timeout is not None:
            exc = self.next_idle_timeout
            self.next_idle_timeout = None
            raise exc
        stdout, rc = self.run_oneshot_return
        return stdout, rc, list(self.progress_ticks)

    # Phase 3: tests that bypass dev_work_sm._run_llm and call
    # ``llm_runner._build_oneshot_cmd`` directly need the same shape the
    # real runner ships. The fake mirrors the real signature but returns
    # a stable, easy-to-assert command list.
    def _build_oneshot_cmd(
        self, agent_type, worktree, timeout_sec,
        task_file=None, prompt=None,
    ):
        cmd: list[str] = [
            "acpx", "--cwd", worktree, "--format", "json",
            "--approve-all", agent_type, "exec",
            "--timeout", str(timeout_sec),
        ]
        if task_file is not None:
            cmd += ["--file", task_file]
        if prompt is not None:
            cmd += ["--prompt", prompt]
        return cmd

    # Phase 9: session-mode call-shape stubs. Tests that drive the SM
    # through ``session_role=`` paths read ``calls`` (kind="session" or
    # "session-prompt") to assert the session name / role / ordering.
    def _resolve_agent(self, agent_type):
        return "claude" if agent_type == "claude" else "codex"

    async def start_session(self, *, name, anchor_cwd, agent, **_execution_kwargs):
        from src.llm_runner import Session

        self.calls.append(dict(
            kind="start_session", name=name, anchor_cwd=anchor_cwd,
            agent=agent,
        ))
        return Session(
            name=name, anchor_cwd=anchor_cwd, agent=agent, created_at="t",
        )

    async def delete_session(self, session):
        self.calls.append(dict(kind="delete_session", name=session.name))

    async def prompt_session_with_progress(
        self, session, *, task_file=None, text=None, timeout_sec,
        heartbeat, heartbeat_interval_s, idle_timeout_s, step_tag,
        **_execution_kwargs,
    ):
        self.calls.append(dict(
            kind="session-prompt", session_name=session.name,
            task_file=task_file, text=text, timeout_sec=timeout_sec,
            step_tag=step_tag,
        ))
        for tick in self.progress_ticks:
            await heartbeat(tick)
        if self.next_idle_timeout is not None:
            exc = self.next_idle_timeout
            self.next_idle_timeout = None
            raise exc
        stdout, rc = self.run_oneshot_return
        return stdout, rc, list(self.progress_ticks)


@pytest.fixture
def fake_llm_runner() -> _FakeLLMRunner:
    return _FakeLLMRunner()
