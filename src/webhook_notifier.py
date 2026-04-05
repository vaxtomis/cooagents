import json
import hmac
import hashlib
import asyncio
import uuid
from datetime import datetime, timezone


# Events that should be pushed to OpenClaw /hooks/agent
OPENCLAW_EVENTS = frozenset({
    "gate.waiting",
    "job.completed",
    "job.failed",
    "job.timeout",
    "job.interrupted",
    "merge.conflict",
    "merge.completed",
    "run.completed",
    "run.cancelled",
    "host.online",
    "host.unavailable",
})


class WebhookNotifier:
    def __init__(self, db, openclaw_hooks=None, trace_emitter=None):
        self.db = db
        self._client = None
        self._openclaw_hooks = openclaw_hooks
        self._trace = trace_emitter

    async def _trace_event(self, event_type, payload=None, level="info", error_detail=None):
        if self._trace:
            await self._trace.emit(event_type, payload, level=level, error_detail=error_detail,
                                   source="webhook")

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
        # 1. Existing generic webhook delivery (unchanged)
        hooks = await self.db.fetchall("SELECT * FROM webhooks WHERE status='active'")
        for hook in hooks:
            h = dict(hook)
            if h.get("events_json"):
                allowed = json.loads(h["events_json"])
                if event_type not in allowed:
                    continue
            await self._deliver_with_retry(h, event_type, payload)

        # 2. OpenClaw hooks delivery (new)
        if (self._openclaw_hooks
                and self._openclaw_hooks.enabled
                and event_type in OPENCLAW_EVENTS):
            await self._deliver_to_openclaw(event_type, payload)

    async def _deliver_to_openclaw(self, event_type, payload):
        """POST event to OpenClaw /hooks/agent endpoint with retry."""
        run_id = payload.get("run_id")
        idem_key = self._make_openclaw_idempotency_key(run_id, event_type)
        failure = None

        for delay in [0, 5, 30]:
            if delay > 0:
                await asyncio.sleep(delay)
            success, failure = await self._deliver_to_openclaw_once(event_type, payload, idem_key)
            if success:
                await self._trace_event("webhook.delivery.success", {"event_type": event_type, "run_id": run_id})
                return

        await self._trace_event("webhook.delivery.failed", {"event_type": event_type, "run_id": run_id, **(failure or {})}, level="error")
        await self._record_openclaw_delivery_failure(run_id, event_type, failure or {})

    def _make_openclaw_idempotency_key(self, run_id, event_type):
        key = f"cooagents:{run_id or 'system'}:{event_type}:{uuid.uuid4().hex}"
        if len(key) > 256:
            key = key[:256]
        return key

    async def _deliver_to_openclaw_once(self, event_type, payload, idem_key):
        """POST a single attempt to OpenClaw /hooks/agent endpoint."""
        cfg = self._openclaw_hooks
        run_id = payload.get("run_id")

        # Look up run for ticket, stage, and per-run notify config
        ticket = payload.get("ticket", "")
        stage = payload.get("current_stage") or payload.get("stage") or payload.get("job_stage") or ""
        channel = cfg.default_channel
        to = cfg.default_to

        if run_id:
            run = await self.db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
            if run:
                ticket = ticket or run.get("ticket", "")
                stage = stage or run.get("current_stage", "")
                channel = run.get("notify_channel") or cfg.default_channel
                to = run.get("notify_to") or cfg.default_to

        # Format structured message.
        # Important: OpenClaw hook ingress wraps webhook payloads in a generic
        # "external/untrusted" envelope that often biases the model toward
        # plain summarization. To make cooagents review gates reliable without
        # changing OpenClaw itself, we embed an explicit, self-contained task
        # contract here so the downstream hook session is much more likely to
        # execute the cooagents workflow skill instead of merely summarizing.
        instruction_lines = [
            "This is a cooagents workflow event.",
            "Do NOT just summarize this webhook.",
            "Treat it as an internal workflow trigger and follow the installed cooagents-workflow skill.",
        ]
        if event_type == "gate.waiting":
            instruction_lines.extend([
                "This event requires action.",
                "If stage is REQ_REVIEW / DESIGN_REVIEW / DEV_REVIEW, you must:",
                "1. Query the run/artifacts from cooagents API.",
                "2. Fetch the latest required artifact content for the gate.",
                "3. Create a Feishu cloud doc with feishu_doc and write the full content.",
                "4. Send the approval message with the doc URL.",
                "Never reply with only a summary when gate.waiting is received.",
            ])
        else:
            instruction_lines.append(
                "Process this event according to the cooagents-workflow skill and deliver the result to the user."
            )

        message = (
            f"Task: cooagents workflow event handler\n"
            f"Required behavior:\n- " + "\n- ".join(instruction_lines) + "\n\n"
            f"Event:\n"
            f"[cooagents:{event_type}] {ticket} {stage}\n"
            f"run_id: {run_id or 'unknown'}\n"
            f"ticket: {ticket}\n"
            f"stage: {stage}"
        )

        body = json.dumps({
            "message": message,
            "name": "cooagents",
            "deliver": True,
            "channel": channel,
            "to": to,
            "wakeMode": "now",
            "idempotencyKey": idem_key,
        })
        headers = {
            "Content-Type": "application/json",
            "Authorization": f"Bearer {cfg.token}",
        }

        try:
            client = await self._get_client()
            resp = await client.post(cfg.url, content=body, headers=headers)
            if not (200 <= resp.status_code < 300):
                # Parse OpenClaw error body: {ok: false, error: "..."}
                resp_body = ""
                try:
                    resp_body = resp.text
                except Exception:
                    pass
                return False, {
                    "event_type": event_type,
                    "status_code": resp.status_code,
                    "response": resp_body[:500],
                }
            return True, None
        except Exception as exc:
            return False, {"event_type": event_type, "error": str(exc)[:200]}

    async def _record_openclaw_delivery_failure(self, run_id, event_type, failure):
        now = datetime.now(timezone.utc).isoformat()
        payload = {"event_type": event_type}
        payload.update(failure or {})
        await self.db.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (run_id, "openclaw.hooks.delivery_failed", json.dumps(payload), now)
        )

    async def _deliver_with_retry(self, webhook, event_type, payload):
        delays = [0, 5, 30]
        for attempt, delay in enumerate(delays):
            if delay > 0:
                await asyncio.sleep(delay)
            success = await self._deliver(webhook, event_type, payload)
            if success:
                return
        now = datetime.now(timezone.utc).isoformat()
        await self.db.execute(
            "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
            (payload.get("run_id"), "webhook.delivery_failed",
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
