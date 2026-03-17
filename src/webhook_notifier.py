import json
import hmac
import hashlib
import asyncio
from datetime import datetime, timezone


class WebhookNotifier:
    def __init__(self, db):
        self.db = db
        self._client = None

    async def _get_client(self):
        if self._client is None:
            import httpx
            self._client = httpx.AsyncClient(timeout=10)
        return self._client

    async def register(self, url, events=None, secret=None) -> int:
        now = datetime.now(timezone.utc).isoformat()
        events_json = json.dumps(events) if events else None
        wid = await self.db.execute(
            "INSERT INTO webhooks(url,events_json,secret,status,created_at,updated_at) VALUES(?,?,?,?,?,?)",
            (url, events_json, secret, "active", now, now)
        )
        return wid

    async def remove(self, webhook_id):
        await self.db.execute("DELETE FROM webhooks WHERE id=?", (webhook_id,))

    async def list_all(self):
        rows = await self.db.fetchall("SELECT * FROM webhooks ORDER BY id")
        return [dict(r) for r in rows]

    async def notify(self, event_type, payload):
        hooks = await self.db.fetchall("SELECT * FROM webhooks WHERE status='active'")
        for hook in hooks:
            h = dict(hook)
            # Check event filter
            if h.get("events_json"):
                allowed = json.loads(h["events_json"])
                if event_type not in allowed:
                    continue
            await self._deliver_with_retry(h, event_type, payload)

    async def _deliver_with_retry(self, webhook, event_type, payload):
        delays = [0, 5, 30]  # retry delays in seconds
        for attempt, delay in enumerate(delays):
            if delay > 0:
                await asyncio.sleep(delay)
            success = await self._deliver(webhook, event_type, payload)
            if success:
                return
        # All retries exhausted
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (payload.get("run_id", "system"), "webhook.delivery_failed",
             json.dumps({"webhook_id": webhook["id"], "event_type": event_type}), now)
        )

    async def _deliver(self, webhook, event_type, payload):
        try:
            client = await self._get_client()
            body = json.dumps({"event": event_type, "payload": payload, "timestamp": datetime.now(timezone.utc).isoformat()})
            headers = {"Content-Type": "application/json"}

            if webhook.get("secret"):
                sig = hmac.new(webhook["secret"].encode(), body.encode(), hashlib.sha256).hexdigest()
                headers["X-Webhook-Signature"] = sig

            resp = await client.post(webhook["url"], content=body, headers=headers)
            return 200 <= resp.status_code < 300
        except Exception:
            return False

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
