import json
import hmac
import hashlib
import asyncio
import os
import uuid
import logging
from datetime import datetime, timezone


log = logging.getLogger(__name__)

_ENV_PREFIX = "$ENV:"


def _resolve_secret(secret):
    """Resolve a stored secret.

    Why: plaintext secrets in SQLite are a standing risk. Values prefixed with
    `$ENV:VARNAME` are redirected to environment variables so operators can
    rotate them without touching the DB. Plain strings still work for
    backwards compatibility but are discouraged.
    """
    if not secret:
        return None
    if isinstance(secret, str) and secret.startswith(_ENV_PREFIX):
        return os.environ.get(secret[len(_ENV_PREFIX):], "") or None
    return secret

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

# Gate → artifact kind / doc title prefix / UI labels
_GATE_ARTIFACT_KIND = {"req": "req", "design": "design", "dev": "test-report"}
_GATE_DOC_PREFIX = {"req": "REQ", "design": "DES", "dev": "TEST-REPORT"}
_GATE_LABEL = {"req": "需求审批", "design": "设计审批", "dev": "开发审批"}
_GATE_DOC_TYPE = {"req": "需求文档", "design": "设计文档", "dev": "测试报告"}
_GATE_NEXT = {"req": "设计阶段", "design": "开发阶段", "dev": "合并阶段"}

# Max artifact content bytes to embed (leave room for message envelope within 256KB limit)
_MAX_ARTIFACT_BYTES = 180_000


class WebhookNotifier:
    def __init__(self, db, openclaw_hooks=None, trace_emitter=None, artifact_manager=None):
        self.db = db
        self._client = None
        self._openclaw_hooks = openclaw_hooks
        self._trace = trace_emitter
        self._artifacts = artifact_manager

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

    async def _fetch_gate_artifact(self, run_id, gate):
        """Pre-fetch the latest artifact content for a review gate.

        Returns a dict with artifact_id, kind, content on success, or None.
        """
        if not self._artifacts or gate not in _GATE_ARTIFACT_KIND:
            return None
        try:
            kind = _GATE_ARTIFACT_KIND[gate]
            arts = await self._artifacts.get_by_run(run_id, kind=kind)
            if not arts:
                return None
            latest = arts[-1]  # ordered by created_at, last = newest
            content = await self._artifacts.get_content(latest["id"])
            # Truncate oversized content to stay within OpenClaw 256KB body limit
            if len(content.encode("utf-8")) > _MAX_ARTIFACT_BYTES:
                content = content[:60_000] + "\n\n…（内容过长，已截断）…"
            return {"artifact_id": latest["id"], "kind": kind, "content": content}
        except Exception as exc:
            log.warning("Failed to pre-fetch artifact for run=%s gate=%s: %s", run_id, gate, exc)
            return None

    def _build_gate_waiting_message(self, ticket, stage, run_id, gate, to, artifact):
        """Build a self-contained action-plan message for gate.waiting events.

        When *artifact* is provided the message embeds the full content so the
        isolated session only needs to call feishu_doc (no curl required).
        When *artifact* is None a fallback message instructs the session to
        query the cooagents API first.
        """
        label = _GATE_LABEL.get(gate, "审批")
        doc_type = _GATE_DOC_TYPE.get(gate, "文档")
        next_stage = _GATE_NEXT.get(gate, "下一阶段")
        doc_title = f"{_GATE_DOC_PREFIX.get(gate, 'DOC')}-{ticket}"

        owner_line = f', "owner_open_id": "{to}"' if to else ""

        approval_tpl = (
            f"📋 {ticket} · {label}\n\n"
            f"📄 {doc_type}：{{url}}\n\n"
            f"请回复：\n"
            f"- \"通过\" — 推进到{next_stage}\n"
            f"- 驳回原因 — 打回给 Agent 修订"
        )

        if artifact:
            # ── Primary path: artifact content pre-fetched ──
            return (
                f"Task: cooagents workflow — gate.waiting → 创建飞书云文档并发送审批消息\n"
                f"Do NOT summarize. Execute the action plan below.\n\n"
                f"Action plan (execute steps 1–3 in order):\n\n"
                f"1. Create Feishu cloud document:\n"
                f'   feishu_doc({{"action": "create", "title": "{doc_title}"{owner_line}}})\n'
                f"   Save the returned doc_token and url.\n\n"
                f"2. Write content to the document:\n"
                f'   feishu_doc({{"action": "write", "doc_token": "{{doc_token from step 1}}", '
                f'"content": "{{artifact content below}}"}})\n\n'
                f"3. Reply with this approval message (auto-delivered to user):\n"
                f"{approval_tpl}\n"
                f"   Replace {{url}} with the url returned in step 1.\n\n"
                f"On feishu_doc failure: reply \"⚠️ 飞书云文件创建失败\" then send approval text without doc URL.\n"
                f"You do not need to wait for user reply — another session handles it.\n\n"
                f"Event:\n"
                f"[cooagents:{gate}.waiting] {ticket} {stage}\n"
                f"run_id: {run_id}\n"
                f"ticket: {ticket}\n"
                f"stage: {stage}\n"
                f"gate: {gate}\n"
                f"notify_to: {to or ''}\n\n"
                f"--- artifact content (for step 2) ---\n"
                f"{artifact['content']}\n"
                f"--- end artifact content ---"
            )
        else:
            # ── Fallback: no artifact available, session must query API ──
            kind = _GATE_ARTIFACT_KIND.get(gate, "req")
            return (
                f"Task: cooagents workflow — gate.waiting → 创建飞书云文档并发送审批消息\n"
                f"Do NOT summarize. Execute the action plan below.\n\n"
                f"Action plan:\n\n"
                f"1. Fetch artifact list:\n"
                f"   exec curl -s http://127.0.0.1:8321/api/v1/runs/{run_id}/artifacts?kind={kind}\n\n"
                f"2. Get content of the latest artifact (highest id):\n"
                f"   exec curl -s http://127.0.0.1:8321/api/v1/runs/{run_id}/artifacts/{{artifact_id}}/content\n\n"
                f"3. Create Feishu cloud document:\n"
                f'   feishu_doc({{"action": "create", "title": "{doc_title}"{owner_line}}})\n'
                f"   Save the returned doc_token and url.\n\n"
                f"4. Write artifact content to the document:\n"
                f'   feishu_doc({{"action": "write", "doc_token": "{{doc_token}}", "content": "{{content from step 2}}"}})\n\n'
                f"5. Reply with this approval message (auto-delivered to user):\n"
                f"{approval_tpl}\n"
                f"   Replace {{url}} with the url returned in step 3.\n\n"
                f"On feishu_doc failure: reply \"⚠️ 飞书云文件创建失败\" then send approval text without doc URL.\n"
                f"You do not need to wait for user reply — another session handles it.\n\n"
                f"Event:\n"
                f"[cooagents:{gate}.waiting] {ticket} {stage}\n"
                f"run_id: {run_id}\n"
                f"ticket: {ticket}\n"
                f"stage: {stage}\n"
                f"gate: {gate}\n"
                f"notify_to: {to or ''}"
            )

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

        # Build message based on event type
        if event_type == "gate.waiting":
            gate = payload.get("gate", "")
            artifact = await self._fetch_gate_artifact(run_id, gate) if run_id else None
            message = self._build_gate_waiting_message(ticket, stage, run_id, gate, to, artifact)
        else:
            message = (
                f"Task: cooagents workflow event handler\n"
                f"Do NOT summarize. Treat as internal workflow trigger and follow cooagents-workflow skill.\n\n"
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

            resolved = _resolve_secret(webhook.get("secret"))
            if resolved:
                sig = hmac.new(resolved.encode(), body.encode(), hashlib.sha256).hexdigest()
                headers["X-Webhook-Signature"] = sig

            resp = await client.post(webhook["url"], content=body, headers=headers)
            return 200 <= resp.status_code < 300
        except Exception:
            return False

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
