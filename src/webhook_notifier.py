"""Outbound webhook delivery (Phase 5).

Single public entry point :py:meth:`WebhookNotifier.deliver`. Internally
dispatches along one of two paths per subscription:

* ``slug == "openclaw"`` — legacy OpenClaw ``/hooks/agent`` envelope with
  ``Authorization: Bearer <token>``. Kept verbatim so the OpenClaw repo
  does not need a single code change. The ``message`` text is simplified
  to ``[cooagents:<event>] ws=<ws>\\npayload: <json-2KB>`` — no more
  feishu_doc action plans.
* Everything else (Hermes, user-registered subscriptions) — PRD-standard
  unified envelope ``{event, event_id, ts, correlation_id, payload}``
  with ``X-Cooagents-Signature: sha256=<hmac-hex>``.

Retries follow ``[0, 5, 30]`` seconds. Three consecutive failures write
a ``webhook.delivery_failed`` row into ``workspace_events``.

Deliveries are fire-and-forget: ``deliver()`` schedules per-subscription
tasks but does not await them. ``close()`` drains all inflight tasks
before the process exits.
"""
from __future__ import annotations

import asyncio
import hashlib
import hmac
import json
import logging
import os
import uuid
from datetime import datetime, timezone
from typing import Any

from src.webhook_events import KNOWN_EVENTS

logger = logging.getLogger(__name__)

_ENV_PREFIX = "$ENV:"

# Retry delays between delivery attempts, in seconds.
_RETRY_DELAYS: tuple[int, ...] = (0, 5, 30)

# OpenClaw receive-side body limit is ~256KB; cap the embedded payload
# text well below it to leave room for the envelope fields.
_OPENCLAW_PAYLOAD_CAP = 2048


def _resolve_secret(secret: str | None) -> str | None:
    """Resolve a stored secret.

    Values prefixed with ``$ENV:VARNAME`` are redirected to environment
    variables so operators can rotate them without touching the DB.
    Plain strings still work for compatibility but are discouraged.
    """
    if not secret:
        return None
    if isinstance(secret, str) and secret.startswith(_ENV_PREFIX):
        return os.environ.get(secret[len(_ENV_PREFIX):], "") or None
    return secret


def _now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _event_allowed(events_json: str | None, event_name: str) -> bool:
    """Return True if this subscription should receive *event_name*.

    ``events_json`` is a JSON-encoded list of event names; ``None`` means
    subscribe to every known event.
    """
    if not events_json:
        return True
    try:
        allowed = json.loads(events_json)
    except (ValueError, TypeError):
        return False
    return isinstance(allowed, list) and event_name in allowed


class WebhookNotifier:
    def __init__(
        self,
        db: Any,
        *,
        settings: Any = None,
    ) -> None:
        self.db = db
        self._settings = settings
        self._client = None
        self._inflight: set[asyncio.Task] = set()

    # ---- HTTP client ----

    async def _get_client(self):
        if self._client is None:
            import httpx

            self._client = httpx.AsyncClient(timeout=10)
        return self._client

    # ---- Subscription management ----

    async def register(
        self,
        url: str,
        *,
        events: list[str] | None = None,
        secret: str | None = None,
        slug: str | None = None,
    ) -> int:
        now = _now_iso()
        events_json = json.dumps(events) if events else None
        return await self.db.execute(
            "INSERT INTO webhook_subscriptions"
            "(slug, url, secret, events_json, active, created_at, updated_at) "
            "VALUES(?,?,?,?,?,?,?)",
            (slug, url, secret, events_json, 1, now, now),
        )

    async def remove(self, sub_id: int) -> None:
        await self.db.execute(
            "DELETE FROM webhook_subscriptions WHERE id=?", (sub_id,)
        )

    async def list_all(self) -> list[dict]:
        rows = await self.db.fetchall(
            "SELECT * FROM webhook_subscriptions ORDER BY id"
        )
        return [dict(r) for r in rows]

    async def bootstrap_builtin_subscriptions(self, settings: Any) -> None:
        """Upsert OpenClaw + Hermes builtin subscriptions from config.

        Idempotent: safe to call on every startup. The ``secret`` column
        stores the Bearer token for ``slug='openclaw'`` rows (not an HMAC
        secret — the OpenClaw path never signs). For every other row it
        stores the HMAC secret used on the generic path.
        """
        self._settings = settings
        openclaw_cfg = getattr(getattr(settings, "openclaw", None), "hooks", None)
        if openclaw_cfg and getattr(openclaw_cfg, "enabled", False):
            await self._upsert_builtin(
                slug="openclaw",
                url=openclaw_cfg.url,
                secret=openclaw_cfg.token,
                events=None,
            )

        hermes_cfg = getattr(getattr(settings, "hermes", None), "webhook", None)
        if hermes_cfg and getattr(hermes_cfg, "enabled", False):
            await self._upsert_builtin(
                slug="hermes",
                url=hermes_cfg.url,
                secret=hermes_cfg.secret,
                events=list(hermes_cfg.events) or None,
            )

    async def _upsert_builtin(
        self,
        *,
        slug: str,
        url: str,
        secret: str | None,
        events: list[str] | None,
    ) -> None:
        now = _now_iso()
        events_json = json.dumps(events) if events else None
        existing = await self.db.fetchone(
            "SELECT id FROM webhook_subscriptions WHERE slug=?", (slug,)
        )
        if existing:
            await self.db.execute(
                "UPDATE webhook_subscriptions "
                "SET url=?, secret=?, events_json=?, active=1, updated_at=? "
                "WHERE slug=?",
                (url, secret, events_json, now, slug),
            )
        else:
            await self.db.execute(
                "INSERT INTO webhook_subscriptions"
                "(slug, url, secret, events_json, active, created_at, updated_at) "
                "VALUES(?,?,?,?,?,?,?)",
                (slug, url, secret, events_json, 1, now, now),
            )

    # ---- Deliver ----

    async def deliver(
        self,
        event_name: str,
        *,
        workspace_id: str | None = None,
        correlation_id: str | None = None,
        payload: dict | None = None,
        event_id: str | None = None,
    ) -> str:
        """Fan out *event_name* to every matching active subscription.

        Returns the envelope ``event_id`` so callers can correlate the
        local ``workspace_events`` row with the outbound envelope.
        """
        assert event_name in KNOWN_EVENTS, (
            f"unknown webhook event {event_name!r}; "
            "register it in src.webhook_events.WebhookEvent"
        )
        eid = event_id or str(uuid.uuid4())
        envelope = {
            "event": event_name,
            "event_id": eid,
            "ts": _now_iso(),
            "correlation_id": correlation_id,
            "payload": payload or {},
        }
        rows = await self.db.fetchall(
            "SELECT * FROM webhook_subscriptions WHERE active=1"
        )
        for row in rows:
            sub = dict(row)
            if not _event_allowed(sub.get("events_json"), event_name):
                continue
            task = asyncio.create_task(
                self._deliver_dispatch(sub, event_name, envelope)
            )
            self._inflight.add(task)
            task.add_done_callback(self._on_task_done)
        return eid

    def _on_task_done(self, task: asyncio.Task) -> None:
        self._inflight.discard(task)
        if task.cancelled():
            return
        exc = task.exception()
        if exc is not None:
            logger.exception(
                "webhook deliver task crashed", exc_info=exc
            )

    async def _deliver_dispatch(
        self,
        sub: dict,
        event_name: str,
        envelope: dict,
    ) -> None:
        if sub.get("slug") == "openclaw":
            await self._with_retry(
                lambda: self._deliver_openclaw(sub, event_name, envelope),
                sub=sub,
                event_name=event_name,
            )
        else:
            await self._with_retry(
                lambda: self._deliver_generic(sub, envelope),
                sub=sub,
                event_name=event_name,
            )

    async def _with_retry(
        self,
        attempt,
        *,
        sub: dict,
        event_name: str,
    ) -> None:
        failure: dict | None = None
        for delay in _RETRY_DELAYS:
            if delay > 0:
                await asyncio.sleep(delay)
            success, failure = await attempt()
            if success:
                return
        await self._record_failure(sub, event_name, failure or {})

    async def _deliver_generic(
        self, sub: dict, envelope: dict
    ) -> tuple[bool, dict | None]:
        body = json.dumps(envelope, ensure_ascii=False)
        headers = {"Content-Type": "application/json"}
        raw_secret = sub.get("secret")
        resolved = _resolve_secret(raw_secret)
        if resolved:
            sig = hmac.new(
                resolved.encode(), body.encode(), hashlib.sha256
            ).hexdigest()
            headers["X-Cooagents-Signature"] = f"sha256={sig}"
        elif raw_secret:
            # Configured-but-empty: $ENV:VAR resolved to "" (env var unset
            # or blank). Sending unsigned would let the receiver silently
            # accept spoofed traffic — log loudly so misconfig is visible.
            logger.warning(
                "webhook subscription id=%s url=%s has secret configured "
                "but resolved empty; delivering UNSIGNED",
                sub.get("id"),
                sub.get("url"),
            )
        try:
            client = await self._get_client()
            resp = await client.post(
                sub["url"], content=body, headers=headers
            )
        except Exception as exc:
            return False, {"url": sub["url"], "error": str(exc)[:200]}
        if 200 <= resp.status_code < 300:
            return True, None
        body_text = ""
        try:
            body_text = resp.text[:500]
        except Exception:
            pass
        return False, {
            "url": sub["url"],
            "status_code": resp.status_code,
            "response": body_text,
        }

    async def _deliver_openclaw(
        self,
        sub: dict,
        event_name: str,
        envelope: dict,
    ) -> tuple[bool, dict | None]:
        cfg = None
        if self._settings is not None:
            cfg = getattr(
                getattr(self._settings, "openclaw", None), "hooks", None
            )
        channel = getattr(cfg, "default_channel", "last") if cfg else "last"
        to = getattr(cfg, "default_to", "") if cfg else ""

        payload_text = json.dumps(envelope["payload"], ensure_ascii=False)
        if len(payload_text) > _OPENCLAW_PAYLOAD_CAP:
            payload_text = payload_text[:_OPENCLAW_PAYLOAD_CAP] + "…"
        ws_id = (
            envelope.get("correlation_id")
            or (envelope.get("payload") or {}).get("workspace_id")
            or ""
        )
        message = (
            f"[cooagents:{event_name}] ws={ws_id}\n"
            f"payload: {payload_text}"
        )
        body = json.dumps(
            {
                "message": message,
                "name": "cooagents",
                "deliver": True,
                "channel": channel,
                "to": to,
                "wakeMode": "now",
                "idempotencyKey": f"cooagents:{envelope['event_id']}",
            },
            ensure_ascii=False,
        )
        raw_secret = sub.get("secret")
        bearer = _resolve_secret(raw_secret) or ""
        if not bearer:
            logger.warning(
                "openclaw subscription id=%s has no resolvable bearer token "
                "(raw=%r); receiver will reject",
                sub.get("id"),
                raw_secret,
            )
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {bearer}",
        }
        try:
            client = await self._get_client()
            resp = await client.post(
                sub["url"], content=body, headers=headers
            )
        except Exception as exc:
            return False, {
                "event": event_name,
                "url": sub["url"],
                "error": str(exc)[:200],
            }
        if 200 <= resp.status_code < 300:
            return True, None
        body_text = ""
        try:
            body_text = resp.text[:500]
        except Exception:
            pass
        return False, {
            "event": event_name,
            "url": sub["url"],
            "status_code": resp.status_code,
            "response": body_text,
        }

    async def _record_failure(
        self,
        sub: dict,
        event_name: str,
        failure: dict,
    ) -> None:
        payload = {
            "event": event_name,
            "subscription_id": sub.get("id"),
            "url": sub.get("url"),
            **(failure or {}),
        }
        correlation = sub.get("slug") or f"sub-{sub.get('id')}"
        await self.db.execute(
            "INSERT INTO workspace_events"
            "(event_id, event_name, workspace_id, correlation_id, payload_json, ts) "
            "VALUES(?,?,?,?,?,?)",
            (
                str(uuid.uuid4()),
                "webhook.delivery_failed",
                None,
                correlation,
                json.dumps(payload, ensure_ascii=False),
                _now_iso(),
            ),
        )

    # ---- Lifecycle ----

    async def close(self) -> None:
        if self._inflight:
            await asyncio.gather(
                *self._inflight, return_exceptions=True
            )
        if self._client:
            await self._client.aclose()
            self._client = None
