from fastapi import APIRouter, Request
from src.models import CreateAgentHostRequest, UpdateAgentHostRequest
from src.exceptions import NotFoundError

router = APIRouter(tags=["agent-hosts"])


@router.get("/agent-hosts")
async def list_hosts(request: Request):
    hm = request.app.state.hosts
    return await hm.list_all()


@router.post("/agent-hosts", status_code=201)
async def create_host(req: CreateAgentHostRequest, request: Request):
    hm = request.app.state.hosts
    await hm.register(req.id, req.host, req.agent_type, req.max_concurrent, req.ssh_key, req.labels)
    hosts = await hm.list_all()
    return next((h for h in hosts if h["id"] == req.id), {})


@router.put("/agent-hosts/{host_id}")
async def update_host(host_id: str, req: UpdateAgentHostRequest, request: Request):
    hm = request.app.state.hosts
    db = request.app.state.db
    existing = await db.fetchone("SELECT * FROM agent_hosts WHERE id=?", (host_id,))
    if not existing:
        raise NotFoundError(f"Host {host_id} not found")
    existing = dict(existing)
    await hm.register(
        host_id,
        req.host or existing["host"],
        req.agent_type or existing["agent_type"],
        req.max_concurrent if req.max_concurrent is not None else existing["max_concurrent"],
        req.ssh_key,
        req.labels
    )
    hosts = await hm.list_all()
    return next((h for h in hosts if h["id"] == host_id), {})


@router.delete("/agent-hosts/{host_id}")
async def delete_host(host_id: str, request: Request):
    hm = request.app.state.hosts
    await hm.remove(host_id)
    return {"ok": True}


@router.post("/agent-hosts/{host_id}/check")
async def check_host(host_id: str, request: Request):
    hm = request.app.state.hosts
    is_online = await hm.health_check(host_id)
    return {"host_id": host_id, "online": is_online}
