"""Agent host CRUD + healthcheck routes (Phase 8a).

Endpoints (all under ``/api/v1``):
  GET    /agent-hosts                   - list every host (sorted by id)
  GET    /agent-hosts/{id}              - fetch one
  POST   /agent-hosts                   - register a new host
  PATCH  /agent-hosts/{id}              - partial update
  DELETE /agent-hosts/{id}              - delete (refuses 'local')
  POST   /agent-hosts/{id}/healthcheck  - run the probe synchronously
  POST   /agent-hosts/sync              - reload config/agents.yaml
"""
from __future__ import annotations

import uuid
from typing import Any

from fastapi import APIRouter, Request, Response

from src.exceptions import BadRequestError, NotFoundError
from src.models import (
    AgentHost,
    CreateAgentHostRequest,
    LOCAL_HOST_ID,
    UpdateAgentHostRequest,
)

router = APIRouter(tags=["agent-hosts"])

# Fields persisted on agent_hosts that must not appear in API responses.
# ssh_key is a server-side filesystem path (e.g. ~/.ssh/id_rsa); leaking it
# tells an attacker exactly which key to steal. Strip at the route layer.
_HIDDEN_RESPONSE_FIELDS: frozenset[str] = frozenset({"ssh_key"})


def _public(row: dict[str, Any]) -> dict[str, Any]:
    return {k: v for k, v in row.items() if k not in _HIDDEN_RESPONSE_FIELDS}


def _check_ssh_key_path(request: Request, ssh_key: str | None) -> None:
    if ssh_key is None:
        return
    settings = request.app.state.settings
    if not settings.agents.is_ssh_key_path_allowed(ssh_key):
        raise BadRequestError(
            f"ssh_key path is outside the configured allow-list "
            f"(agents.ssh_key_allowed_roots)"
        )


@router.get("/agent-hosts", response_model=list[AgentHost])
async def list_hosts(request: Request) -> list[dict[str, Any]]:
    repo = request.app.state.agent_host_repo
    rows = await repo.list_all()
    return [_public(r) for r in rows]


@router.get("/agent-hosts/{host_id}", response_model=AgentHost)
async def get_host(host_id: str, request: Request) -> dict[str, Any]:
    repo = request.app.state.agent_host_repo
    row = await repo.get(host_id)
    if row is None:
        raise NotFoundError(f"agent host not found: {host_id!r}")
    return _public(row)


@router.post("/agent-hosts", status_code=201, response_model=AgentHost)
async def create_host(
    payload: CreateAgentHostRequest, request: Request, response: Response,
) -> dict[str, Any]:
    repo = request.app.state.agent_host_repo
    host_id = payload.id if payload.id is not None else f"ah-{uuid.uuid4().hex[:12]}"
    if host_id == LOCAL_HOST_ID:
        raise BadRequestError(
            "id 'local' is reserved; pick a different id or omit to auto-allocate"
        )
    if await repo.get(host_id) is not None:
        raise BadRequestError(f"agent host id already exists: {host_id!r}")
    _check_ssh_key_path(request, payload.ssh_key)
    row = await repo.upsert(
        id=host_id,
        host=payload.host,
        agent_type=payload.agent_type,
        max_concurrent=payload.max_concurrent,
        ssh_key=payload.ssh_key,
        labels=payload.labels,
    )
    response.headers["Location"] = f"/api/v1/agent-hosts/{host_id}"
    return _public(row)


@router.patch("/agent-hosts/{host_id}", response_model=AgentHost)
async def update_host(
    host_id: str, payload: UpdateAgentHostRequest, request: Request,
) -> dict[str, Any]:
    repo = request.app.state.agent_host_repo
    row = await repo.get(host_id)
    if row is None:
        raise NotFoundError(f"agent host not found: {host_id!r}")
    _check_ssh_key_path(request, payload.ssh_key)
    merged = {
        "id": host_id,
        "host": payload.host if payload.host is not None else row["host"],
        "agent_type": (
            payload.agent_type if payload.agent_type is not None
            else row["agent_type"]
        ),
        "max_concurrent": (
            payload.max_concurrent if payload.max_concurrent is not None
            else row["max_concurrent"]
        ),
        "ssh_key": payload.ssh_key if payload.ssh_key is not None else row.get("ssh_key"),
        "labels": payload.labels if payload.labels is not None else row.get("labels", []),
    }
    return _public(await repo.upsert(**merged))


@router.delete("/agent-hosts/{host_id}", status_code=204)
async def delete_host(host_id: str, request: Request) -> Response:
    repo = request.app.state.agent_host_repo
    await repo.delete(host_id)  # repo raises NotFoundError / BadRequestError
    return Response(status_code=204)


@router.post("/agent-hosts/{host_id}/healthcheck", response_model=AgentHost)
async def healthcheck(host_id: str, request: Request) -> dict[str, Any]:
    repo = request.app.state.agent_host_repo
    dispatcher = request.app.state.ssh_dispatcher
    if await repo.get(host_id) is None:
        raise NotFoundError(f"agent host not found: {host_id!r}")
    result = await dispatcher.healthcheck(host_id)
    await repo.update_health(
        host_id,
        status=result["health_status"],
        err=result.get("last_health_err"),
    )
    refreshed = await repo.get(host_id)
    if refreshed is None:
        raise NotFoundError(f"agent host not found: {host_id!r}")
    return _public(refreshed)


@router.post("/agent-hosts/sync")
async def sync_from_config(request: Request) -> dict:
    """Reload ``config/agents.yaml`` and reconcile with the DB."""
    repo = request.app.state.agent_host_repo
    settings = request.app.state.settings
    return await repo.sync_from_config(settings.agents)
