"""Webhook subscription CRUD + delivery failure log (Phase 5)."""
from __future__ import annotations

import json

from fastapi import APIRouter, Request

from src.exceptions import BadRequestError, NotFoundError
from src.models import CreateWebhookSubscriptionRequest

router = APIRouter(tags=["webhooks"])

_BUILTIN_SLUGS = frozenset({"openclaw", "hermes"})


@router.post("/webhooks", status_code=201)
async def create_webhook(
    req: CreateWebhookSubscriptionRequest, request: Request
):
    wn = request.app.state.webhooks
    sub_id = await wn.register(
        req.url, events=req.events, secret=req.secret, slug=req.slug
    )
    subs = await wn.list_all()
    return next((s for s in subs if s["id"] == sub_id), {})


@router.get("/webhooks")
async def list_webhooks(request: Request):
    wn = request.app.state.webhooks
    return await wn.list_all()


@router.delete("/webhooks/{sub_id}")
async def delete_webhook(sub_id: int, request: Request):
    db = request.app.state.db
    row = await db.fetchone(
        "SELECT slug FROM webhook_subscriptions WHERE id=?", (sub_id,)
    )
    if row is None:
        raise NotFoundError(f"webhook subscription {sub_id} not found")
    if row.get("slug") in _BUILTIN_SLUGS:
        raise BadRequestError(
            f"builtin subscription {row['slug']!r} cannot be deleted; "
            "disable via config instead"
        )
    wn = request.app.state.webhooks
    await wn.remove(sub_id)
    return {"ok": True}


@router.get("/webhooks/{sub_id}/deliveries")
async def get_deliveries(sub_id: int, request: Request):
    db = request.app.state.db
    # Push the subscription_id filter into SQL via LIKE on payload_json so
    # we don't lose hits to a fixed-size pre-filter window when many other
    # subscriptions are also failing. The exact-match Python check below
    # is kept as defense against substring collisions (e.g. id=1 matching
    # subscription_id=10).
    # json.dumps default uses ", " / ": " separators; match the writer.
    needle = f'%"subscription_id": {sub_id}%'
    rows = await db.fetchall(
        "SELECT event_id, event_name, correlation_id, payload_json, ts "
        "FROM workspace_events "
        "WHERE event_name='webhook.delivery_failed' "
        "AND payload_json LIKE ? "
        "ORDER BY ts DESC LIMIT 50",
        (needle,),
    )
    deliveries: list[dict] = []
    for row in rows:
        record = dict(row)
        try:
            payload = json.loads(record.get("payload_json") or "{}")
        except json.JSONDecodeError:
            continue
        if payload.get("subscription_id") != sub_id:
            continue
        record["payload"] = payload
        deliveries.append(record)
    return deliveries
