from fastapi import APIRouter, Request
from src.models import CreateWebhookRequest
from src.exceptions import NotFoundError

router = APIRouter(tags=["webhooks"])


@router.post("/webhooks", status_code=201)
async def create_webhook(req: CreateWebhookRequest, request: Request):
    wn = request.app.state.webhooks
    wid = await wn.register(req.url, req.events, req.secret)
    hooks = await wn.list_all()
    return next((h for h in hooks if h["id"] == wid), {})


@router.get("/webhooks")
async def list_webhooks(request: Request):
    wn = request.app.state.webhooks
    return await wn.list_all()


@router.delete("/webhooks/{webhook_id}")
async def delete_webhook(webhook_id: int, request: Request):
    wn = request.app.state.webhooks
    await wn.remove(webhook_id)
    return {"ok": True}


@router.get("/webhooks/{webhook_id}/deliveries")
async def get_deliveries(webhook_id: int, request: Request):
    db = request.app.state.db
    rows = await db.fetchall(
        "SELECT * FROM events WHERE event_type='webhook.delivery_failed' ORDER BY created_at DESC LIMIT 50"
    )
    return [dict(r) for r in rows]
