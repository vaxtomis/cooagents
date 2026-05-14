"""SSH dispatcher for agent hosts (Phase 8a healthcheck only).

Phase 8a: ``healthcheck`` runs the three-step probe (connect, ``acpx
--version``, ``test -w <workspaces_root>``). ``run_remote`` is a stub that
raises ``NotImplementedError("Phase 8b")`` so dispatch lifecycle wiring can
be exercised end-to-end without remote execution.
"""
from __future__ import annotations

import asyncio
import logging
import shlex
from pathlib import Path
from typing import TYPE_CHECKING, Any

from src.exceptions import NotFoundError
from src.models import LOCAL_HOST_ID

if TYPE_CHECKING:  # avoid heavy import at module top
    from src.agent_hosts.repo import AgentHostRepo

logger = logging.getLogger(__name__)


def _parse_ssh_target(host: str) -> tuple[str, str, int]:
    """Split ``user@host[:port]`` into (user, hostname, port)."""
    if "@" not in host:
        raise ValueError(f"invalid ssh target: {host!r}")
    user, rest = host.split("@", 1)
    if ":" in rest:
        hostname, port_s = rest.rsplit(":", 1)
        port = int(port_s)
    else:
        hostname, port = rest, 22
    return user, hostname, port


class SshDispatcher:
    """Healthcheck + (Phase 8b) remote run for an agent host."""

    def __init__(
        self,
        repo: "AgentHostRepo",
        *,
        ssh_timeout_s: int = 5,
        strict_host_key: bool = True,
        known_hosts_path: str | Path | None = None,
        workspaces_root: str | Path = "~/cooagents-workspace",
    ) -> None:
        self.repo = repo
        self.ssh_timeout_s = ssh_timeout_s
        self.strict_host_key = strict_host_key
        # When strict, asyncssh needs a *path* to known_hosts. Empty/missing
        # path with strict=True would silently reject every connection, which
        # is the worst of both worlds.
        self._known_hosts_path: str | None = (
            str(Path(known_hosts_path).expanduser())
            if known_hosts_path is not None else None
        )
        # Resolved at use time so tests can monkeypatch HOME.
        self._workspaces_root = str(workspaces_root)

    async def healthcheck(self, host_id: str) -> dict[str, Any]:
        """Return ``{health_status, last_health_err}`` for the host.

        Errors are turned into ``health_status='unhealthy'`` + a short
        error tag instead of being raised — the probe loop must keep going
        even if one host is broken.
        """
        host = await self.repo.get(host_id)
        if host is None:
            raise NotFoundError(f"agent host not found: {host_id!r}")
        if host["host"] == LOCAL_HOST_ID:
            return await self._healthcheck_local()
        return await self._healthcheck_ssh(host)

    async def _healthcheck_local(self) -> dict[str, Any]:
        # Local host is always healthy as long as the cooagents process is
        # running — there is no SSH layer to fail. We still confirm acpx is
        # callable so an operator who forgot to install it sees red.
        try:
            proc = await asyncio.create_subprocess_exec(
                "acpx", "--version",
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                rc = await asyncio.wait_for(
                    proc.wait(), timeout=self.ssh_timeout_s
                )
            except asyncio.TimeoutError:
                proc.kill()
                # Reap the killed child so its FDs are released. Bounded
                # so a wedged kernel doesn't block the probe loop forever.
                try:
                    await asyncio.wait_for(proc.wait(), timeout=1)
                except asyncio.TimeoutError:
                    logger.warning(
                        "acpx --version did not exit after kill; FDs may leak"
                    )
                return {
                    "health_status": "unhealthy",
                    "last_health_err": "acpx_version_timeout",
                }
            if rc != 0:
                return {
                    "health_status": "unhealthy",
                    "last_health_err": f"acpx_not_found: rc={rc}",
                }
        except FileNotFoundError:
            return {
                "health_status": "unhealthy",
                "last_health_err": "acpx_not_installed",
            }
        return {"health_status": "healthy", "last_health_err": None}

    async def _healthcheck_ssh(self, host: dict[str, Any]) -> dict[str, Any]:
        try:
            import asyncssh  # type: ignore[import-not-found]
        except ImportError:  # pragma: no cover — declared in requirements.txt
            return {
                "health_status": "unhealthy",
                "last_health_err": "asyncssh_not_installed",
            }

        try:
            user, hostname, port = _parse_ssh_target(host["host"])
        except ValueError as exc:
            return {
                "health_status": "unhealthy",
                "last_health_err": f"bad_ssh_target: {exc}",
            }

        # asyncssh contract:
        #   known_hosts=None   -> no verification (insecure)
        #   known_hosts=<path> -> verify against that file (strict, secure)
        #   known_hosts=()     -> verify against an empty set (rejects all)
        # Strict + missing path = rejects everything; treat that as a config
        # error rather than silently failing every probe.
        if self.strict_host_key and not self._known_hosts_path:
            return {
                "health_status": "unhealthy",
                "last_health_err": "strict_mode_missing_known_hosts_path",
            }
        connect_kwargs: dict[str, Any] = {
            "host": hostname,
            "port": port,
            "username": user,
            "known_hosts": (
                self._known_hosts_path if self.strict_host_key else None
            ),
        }
        if host.get("ssh_key"):
            connect_kwargs["client_keys"] = [host["ssh_key"]]

        try:
            return await asyncio.wait_for(
                self._run_probe_steps(asyncssh, connect_kwargs),
                timeout=self.ssh_timeout_s * 3,  # 3 steps × per-step timeout
            )
        except asyncio.TimeoutError:
            return {
                "health_status": "unhealthy",
                "last_health_err": "healthcheck_timeout",
            }
        except Exception as exc:  # asyncssh raises a wide tree
            logger.exception("ssh healthcheck failed for %s", host["id"])
            return {
                "health_status": "unhealthy",
                "last_health_err": f"ssh_connect_failed: {type(exc).__name__}",
            }

    async def _run_probe_steps(
        self, asyncssh: Any, connect_kwargs: dict[str, Any]
    ) -> dict[str, Any]:
        async with asyncssh.connect(**connect_kwargs) as conn:
            r = await conn.run("acpx --version", check=False)
            if r.exit_status != 0:
                return {
                    "health_status": "unhealthy",
                    "last_health_err": f"acpx_not_found: rc={r.exit_status}",
                }
            r = await conn.run(
                f"test -w {shlex.quote(self._workspaces_root)}",
                check=False,
            )
            if r.exit_status != 0:
                return {
                    "health_status": "unhealthy",
                    "last_health_err": "workspaces_root_not_writable",
                }
        return {"health_status": "healthy", "last_health_err": None}

    async def run_remote(
        self,
        host_id: str,
        *,
        cmd: list[str],
        cwd: str,
        timeout: int,
        workspace_id: str | None = None,
        correlation_id: str | None = None,
        task_file: str | None = None,
        agent: str | None = None,
        execution_id: str | None = None,
        run_token: str | None = None,
        session_name: str | None = None,
        **_extra: Any,
    ) -> tuple[str, int]:
        """SSH into ``host_id`` and run ``cooagents-worker run ...``.

        ``cmd`` is the legacy local-acpx command line that
        :class:`AcpxExecutor` would have run on the cooagents host. We
        ignore it here in favour of the structured ``workspace_id /
        task_file / agent / timeout`` kwargs because the worker takes
        responsibility for assembling its own acpx invocation in the
        agent-host environment (different ``WORKSPACES_ROOT``, different
        env vars, etc.).
        """
        if workspace_id is None or task_file is None or agent is None:
            raise ValueError(
                "run_remote requires workspace_id, task_file, agent kwargs"
            )

        host = await self.repo.get(host_id)
        if host is None:
            raise NotFoundError(f"agent host not found: {host_id!r}")
        if host["host"] == LOCAL_HOST_ID:
            raise ValueError(
                "run_remote called for local host; should not happen"
            )

        try:
            import asyncssh  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover — declared in requirements.txt
            raise RuntimeError("asyncssh is not installed") from exc

        try:
            user, hostname, port = _parse_ssh_target(host["host"])
        except ValueError as exc:
            raise RuntimeError(f"bad ssh target for {host_id!r}: {exc}") from exc

        if self.strict_host_key and not self._known_hosts_path:
            raise RuntimeError(
                f"strict_host_key=True but ssh_known_hosts_path is unset; "
                f"refusing to connect to {host_id!r}"
            )

        connect_kwargs: dict[str, Any] = {
            "host": hostname,
            "port": port,
            "username": user,
            "known_hosts": (
                self._known_hosts_path if self.strict_host_key else None
            ),
        }
        if host.get("ssh_key"):
            connect_kwargs["client_keys"] = [host["ssh_key"]]

        # Build the worker command. asyncssh.run takes a single string and
        # passes it to the remote shell; quote every user-supplied value.
        worker_parts = [
            "cooagents-worker", "run",
            "--workspace-id", shlex.quote(workspace_id),
            "--task-file", shlex.quote(task_file),
            "--agent", shlex.quote(agent),
            "--timeout", str(int(timeout)),
            "--correlation-id", shlex.quote(correlation_id or ""),
        ]
        if execution_id:
            worker_parts += ["--execution-id", shlex.quote(execution_id)]
        if run_token:
            worker_parts += ["--run-token", shlex.quote(run_token)]
        worker_parts += ["--host-id", shlex.quote(host_id)]
        if session_name:
            worker_parts += ["--session-name", shlex.quote(session_name)]
        worker_cmd = " ".join(worker_parts)
        logger.info(
            "ssh run_remote host=%s workspace=%s correlation=%s agent=%s",
            host_id, workspace_id, correlation_id, agent,
        )

        # Outer timeout = the per-step timeout plus a small grace window so
        # the worker has a chance to report acpx exceeding its own budget.
        outer_timeout = max(timeout + 30, timeout)
        try:
            async with asyncssh.connect(**connect_kwargs) as conn:
                result = await asyncio.wait_for(
                    conn.run(worker_cmd, check=False),
                    timeout=outer_timeout,
                )
        except asyncio.TimeoutError:
            return ("", 124)
        stdout = (result.stdout or "")
        rc = int(result.exit_status or 0)
        return stdout.strip(), rc

    async def cleanup_remote(
        self,
        host_id: str,
        *,
        terminate_grace_s: int = 15,
        kill_grace_s: int = 10,
        kill_enabled: bool = True,
        limit: int = 50,
        timeout_s: int = 60,
    ) -> tuple[str, int]:
        """Run one cleanup pass on the remote host via ``cooagents-worker``."""
        host = await self.repo.get(host_id)
        if host is None:
            raise NotFoundError(f"agent host not found: {host_id!r}")
        if host["host"] == LOCAL_HOST_ID:
            raise ValueError("cleanup_remote called for local host")

        try:
            import asyncssh  # type: ignore[import-not-found]
        except ImportError as exc:  # pragma: no cover
            raise RuntimeError("asyncssh is not installed") from exc

        try:
            user, hostname, port = _parse_ssh_target(host["host"])
        except ValueError as exc:
            raise RuntimeError(f"bad ssh target for {host_id!r}: {exc}") from exc

        if self.strict_host_key and not self._known_hosts_path:
            raise RuntimeError(
                f"strict_host_key=True but ssh_known_hosts_path is unset; "
                f"refusing to connect to {host_id!r}"
            )

        connect_kwargs: dict[str, Any] = {
            "host": hostname,
            "port": port,
            "username": user,
            "known_hosts": (
                self._known_hosts_path if self.strict_host_key else None
            ),
        }
        if host.get("ssh_key"):
            connect_kwargs["client_keys"] = [host["ssh_key"]]

        parts = [
            "cooagents-worker", "cleanup-once",
            "--host-id", shlex.quote(host_id),
            "--limit", str(int(limit)),
            "--terminate-grace", str(float(terminate_grace_s)),
            "--kill-grace", str(float(kill_grace_s)),
        ]
        if not kill_enabled:
            parts.append("--no-kill")
        cmd = " ".join(parts)
        logger.info("ssh cleanup_remote host=%s", host_id)
        try:
            async with asyncssh.connect(**connect_kwargs) as conn:
                result = await asyncio.wait_for(
                    conn.run(cmd, check=False),
                    timeout=timeout_s,
                )
        except asyncio.TimeoutError:
            return ("", 124)
        return (result.stdout or "").strip(), int(result.exit_status or 0)
