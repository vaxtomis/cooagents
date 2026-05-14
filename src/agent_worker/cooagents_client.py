"""HTTP client for the cooagents control plane.

Wraps an ``httpx.AsyncClient`` so the worker can fetch the workspace_files
index and write back diff outputs without depending on FastAPI internals.
``X-Agent-Token`` carries the AGENT_API_TOKEN — the same shared-secret path
OpenClaw uses (see plan AD6).
"""
from __future__ import annotations

import logging
from typing import Any

import httpx

logger = logging.getLogger(__name__)


class CooagentsClientError(RuntimeError):
    """Surface non-2xx responses from cooagents to the worker CLI."""

    def __init__(self, message: str, *, status_code: int, body: Any = None):
        super().__init__(message)
        self.status_code = status_code
        self.body = body


class CooagentsClient:
    """Thin wrapper over ``httpx.AsyncClient`` for worker → cooagents calls."""

    def __init__(
        self,
        *,
        base_url: str,
        agent_token: str,
        timeout: float = 30.0,
        client: httpx.AsyncClient | None = None,
    ) -> None:
        if not base_url.startswith(("http://", "https://")):
            raise ValueError(
                f"base_url must be an http(s) URL, got {base_url!r}"
            )
        self._base_url = base_url.rstrip("/")
        self._headers = {"X-Agent-Token": agent_token}
        self._owns_client = client is None
        self._client = client or httpx.AsyncClient(
            base_url=self._base_url,
            headers=self._headers,
            timeout=timeout,
        )

    async def __aenter__(self) -> "CooagentsClient":
        return self

    async def __aexit__(self, exc_type, exc, tb) -> None:
        await self.close()

    async def close(self) -> None:
        if self._owns_client:
            await self._client.aclose()

    async def get_files_index(
        self, workspace_id: str
    ) -> dict[str, Any]:
        """Return ``{workspace_id, slug, files: [...]}`` for materialize."""
        path = f"/api/v1/workspaces/{workspace_id}/files"
        resp = await self._client.get(path)
        self._raise_for_status(resp, path)
        return resp.json()

    async def mark_execution_started(
        self,
        execution_id: str,
        *,
        pid: int,
        pgid: int | None,
        pid_starttime: str | None,
        cwd: str,
        worker_pid: int | None = None,
        worker_pid_starttime: str | None = None,
    ) -> dict[str, Any]:
        path = f"/api/v1/internal/agent-executions/{execution_id}/started"
        resp = await self._client.post(
            path,
            json={
                "pid": pid,
                "pgid": pgid,
                "pid_starttime": pid_starttime,
                "cwd": cwd,
                "worker_pid": worker_pid,
                "worker_pid_starttime": worker_pid_starttime,
            },
        )
        self._raise_for_status(resp, path)
        return resp.json()

    async def heartbeat_execution(
        self, execution_id: str,
    ) -> dict[str, Any]:
        path = f"/api/v1/internal/agent-executions/{execution_id}/heartbeat"
        resp = await self._client.post(path)
        self._raise_for_status(resp, path)
        return resp.json()

    async def mark_execution_exited(
        self, execution_id: str, *, exit_code: int | None,
    ) -> dict[str, Any]:
        path = f"/api/v1/internal/agent-executions/{execution_id}/exited"
        resp = await self._client.post(path, json={"exit_code": exit_code})
        self._raise_for_status(resp, path)
        return resp.json()

    async def list_expired_executions(
        self, *, host_id: str, limit: int = 50,
    ) -> list[dict[str, Any]]:
        path = "/api/v1/internal/agent-executions/expired"
        resp = await self._client.get(
            path, params={"host_id": host_id, "limit": limit},
        )
        self._raise_for_status(resp, path)
        return list(resp.json())

    async def mark_cleanup_result(
        self,
        execution_id: str,
        *,
        state: str,
        exit_code: int | None = None,
        cleanup_reason: str | None = None,
    ) -> dict[str, Any]:
        path = f"/api/v1/internal/agent-executions/{execution_id}/cleanup-result"
        resp = await self._client.post(
            path,
            json={
                "state": state,
                "exit_code": exit_code,
                "cleanup_reason": cleanup_reason,
            },
        )
        self._raise_for_status(resp, path)
        return resp.json()

    async def post_file(
        self,
        workspace_id: str,
        *,
        relative_path: str,
        kind: str,
        payload: bytes,
        expected_prior_hash: str | None | object,
    ) -> dict[str, Any]:
        """POST a single file diff back to cooagents.

        ``expected_prior_hash``:
          * The CooagentsClient sentinel ``CAS_NONE`` (a literal ``None``
            module-level alias) means "first write"; sent as
            ``X-Expected-Prior-Hash: none``.
          * Any string is sent verbatim.
          * Anything else (including the absence of CAS) is rejected — the
            worker must always assert.
        """
        path = f"/api/v1/workspaces/{workspace_id}/files"
        headers: dict[str, str] = {}
        if expected_prior_hash is None:
            headers["X-Expected-Prior-Hash"] = "none"
        elif isinstance(expected_prior_hash, str):
            headers["X-Expected-Prior-Hash"] = expected_prior_hash
        else:
            raise ValueError(
                "expected_prior_hash must be None or a hex string; "
                f"got {expected_prior_hash!r}"
            )
        files = {"file": (relative_path, payload, "application/octet-stream")}
        data = {"relative_path": relative_path, "kind": kind}
        resp = await self._client.post(
            path, headers=headers, files=files, data=data
        )
        self._raise_for_status(resp, path)
        return resp.json()

    @staticmethod
    def _raise_for_status(resp: httpx.Response, path: str) -> None:
        if resp.is_success:
            return
        try:
            body: Any = resp.json()
        except ValueError:
            body = resp.text
        logger.warning(
            "cooagents %s -> %d: %s", path, resp.status_code, body
        )
        raise CooagentsClientError(
            f"{path} -> HTTP {resp.status_code}",
            status_code=resp.status_code,
            body=body,
        )
