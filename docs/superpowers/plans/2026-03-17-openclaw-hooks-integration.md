# OpenClaw Hooks Integration Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Enable cooagents webhook events to push to OpenClaw `/hooks/agent` endpoint so that the OpenClaw Agent (with cooagents-workflow Skill) can automatically notify users and handle approvals.

**Architecture:** WebhookNotifier gains a second delivery path alongside existing generic webhooks. When `openclaw.hooks.enabled` is true, filtered events are formatted as structured text and POSTed to `/hooks/agent` with Bearer auth. Per-run `notify_channel`/`notify_to` override global defaults. The SKILL.md is updated so Agent knows how to handle incoming webhook messages and approval replies.

**Tech Stack:** Python 3.12, FastAPI, aiosqlite, httpx, Pydantic, pytest

---

### Task 1: Schema — Add notify columns to runs table

**Files:**
- Modify: `db/schema.sql:4-19`
- Test: `tests/test_openclaw_hooks.py` (create)

- [ ] **Step 1: Write the failing test**

Create `tests/test_openclaw_hooks.py` with a test that creates a run row with `notify_channel` and `notify_to` columns:

```python
import pytest
from src.database import Database


@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


async def test_runs_table_has_notify_columns(db):
    """Schema should include notify_channel and notify_to in runs table."""
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,"
        "notify_channel,notify_to,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?,?,?)",
        ("r1", "T-1", "/repo", "running", "INIT", "feishu", "ou_abc123",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )
    row = await db.fetchone("SELECT notify_channel, notify_to FROM runs WHERE id='r1'")
    assert row["notify_channel"] == "feishu"
    assert row["notify_to"] == "ou_abc123"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_openclaw_hooks.py::test_runs_table_has_notify_columns -v`
Expected: FAIL — `notify_channel` column does not exist

- [ ] **Step 3: Add columns to schema**

In `db/schema.sql`, add two columns after `preferences_json` in the `runs` table:

```sql
  notify_channel  TEXT,
  notify_to       TEXT,
```

- [ ] **Step 4: Run test to verify it passes**

Run: `python -m pytest tests/test_openclaw_hooks.py::test_runs_table_has_notify_columns -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add db/schema.sql tests/test_openclaw_hooks.py
git commit -m "feat: add notify_channel/notify_to columns to runs table"
```

---

### Task 2: Config — Add OpenclawHooksConfig

**Files:**
- Modify: `src/config.py:62-64`
- Modify: `config/settings.yaml:35-40`
- Test: `tests/test_openclaw_hooks.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_openclaw_hooks.py`:

```python
from src.config import load_settings, Settings, OpenclawHooksConfig


def test_openclaw_hooks_config_defaults():
    """OpenclawHooksConfig should have sensible defaults."""
    cfg = OpenclawHooksConfig()
    assert cfg.enabled is False
    assert cfg.url == "http://127.0.0.1:18789/hooks/agent"
    assert cfg.token == ""
    assert cfg.default_channel == "feishu"
    assert cfg.default_to == ""


def test_settings_has_openclaw_hooks():
    """Settings.openclaw should include a hooks sub-config."""
    s = Settings()
    assert hasattr(s.openclaw, "hooks")
    assert isinstance(s.openclaw.hooks, OpenclawHooksConfig)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_openclaw_hooks.py::test_openclaw_hooks_config_defaults tests/test_openclaw_hooks.py::test_settings_has_openclaw_hooks -v`
Expected: FAIL — `OpenclawHooksConfig` does not exist

- [ ] **Step 3: Add OpenclawHooksConfig to config.py**

In `src/config.py`, add after the `OpenclawTarget` class (before `OpenclawConfig`):

```python
class OpenclawHooksConfig(BaseModel):
    enabled: bool = False
    url: str = "http://127.0.0.1:18789/hooks/agent"
    token: str = ""
    default_channel: str = "feishu"
    default_to: str = ""
```

Add `hooks` field to `OpenclawConfig`:

```python
class OpenclawConfig(BaseModel):
    deploy_skills: bool = True
    targets: list[OpenclawTarget] = []
    hooks: OpenclawHooksConfig = OpenclawHooksConfig()
```

- [ ] **Step 4: Update settings.yaml**

Add `hooks` subsection under `openclaw:` in `config/settings.yaml`:

```yaml
openclaw:
  deploy_skills: true
  targets:
    - type: local
      skills_dir: "~/.openclaw/skills"
  hooks:
    enabled: false
    url: "http://127.0.0.1:18789/hooks/agent"
    token: ""
    default_channel: "feishu"
    default_to: ""
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_openclaw_hooks.py::test_openclaw_hooks_config_defaults tests/test_openclaw_hooks.py::test_settings_has_openclaw_hooks -v`
Expected: PASS

- [ ] **Step 6: Commit**

```bash
git add src/config.py config/settings.yaml tests/test_openclaw_hooks.py
git commit -m "feat: add OpenclawHooksConfig to config and settings"
```

---

### Task 3: API Model + State Machine — Store notify fields on create_run

**Files:**
- Modify: `src/models.py:26-31`
- Modify: `src/state_machine.py:82-113`
- Modify: `routes/runs.py:12-15`
- Test: `tests/test_openclaw_hooks.py`

- [ ] **Step 1: Write the failing test**

Append to `tests/test_openclaw_hooks.py`:

```python
from unittest.mock import AsyncMock
from src.artifact_manager import ArtifactManager
from src.state_machine import StateMachine


@pytest.fixture
async def sm(db):
    am = ArtifactManager(db)
    am.render_task = AsyncMock(return_value="task-path")
    webhook = AsyncMock()
    webhook.notify = AsyncMock()
    executor = AsyncMock()
    host_mgr = AsyncMock()
    merge_mgr = AsyncMock()
    return StateMachine(db, am, host_mgr, executor, webhook, merge_mgr)


async def test_create_run_stores_notify_fields(sm, db):
    """create_run should persist notify_channel and notify_to."""
    run = await sm.create_run(
        "T-1", "/repo",
        notify_channel="feishu", notify_to="ou_abc123",
    )
    row = await db.fetchone("SELECT notify_channel, notify_to FROM runs WHERE id=?", (run["id"],))
    assert row["notify_channel"] == "feishu"
    assert row["notify_to"] == "ou_abc123"


async def test_create_run_notify_fields_optional(sm, db):
    """create_run without notify fields should store NULL."""
    run = await sm.create_run("T-2", "/repo")
    row = await db.fetchone("SELECT notify_channel, notify_to FROM runs WHERE id=?", (run["id"],))
    assert row["notify_channel"] is None
    assert row["notify_to"] is None
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_openclaw_hooks.py::test_create_run_stores_notify_fields tests/test_openclaw_hooks.py::test_create_run_notify_fields_optional -v`
Expected: FAIL — `create_run()` doesn't accept `notify_channel`/`notify_to`

- [ ] **Step 3: Add fields to CreateRunRequest**

In `src/models.py`, add to `CreateRunRequest`:

```python
class CreateRunRequest(BaseModel):
    ticket: str
    repo_path: str
    description: str | None = None
    preferences: dict | None = None
    notify_channel: str | None = None
    notify_to: str | None = None
```

- [ ] **Step 4: Update state_machine.create_run()**

In `src/state_machine.py`, update `create_run` signature and INSERT:

```python
async def create_run(
    self,
    ticket: str,
    repo_path: str,
    description: str | None = None,
    preferences: dict | None = None,
    notify_channel: str | None = None,
    notify_to: str | None = None,
) -> dict:
```

Update the INSERT statement:

```python
await self.db.execute(
    "INSERT INTO runs(id,ticket,repo_path,status,current_stage,"
    "description,preferences_json,notify_channel,notify_to,created_at,updated_at) "
    "VALUES(?,?,?,?,?,?,?,?,?,?,?)",
    (run_id, ticket, repo_path, "running", "INIT", description, prefs,
     notify_channel, notify_to, now, now),
)
```

- [ ] **Step 5: Update route to pass fields through**

In `routes/runs.py`, update the `create_run` endpoint:

```python
@router.post("/runs", status_code=201)
async def create_run(req: CreateRunRequest, request: Request):
    sm = request.app.state.sm
    result = await sm.create_run(
        req.ticket, req.repo_path, req.description, req.preferences,
        notify_channel=req.notify_channel, notify_to=req.notify_to,
    )
    return result
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_openclaw_hooks.py::test_create_run_stores_notify_fields tests/test_openclaw_hooks.py::test_create_run_notify_fields_optional -v`
Expected: PASS

- [ ] **Step 7: Run existing state machine tests to verify no regression**

Run: `python -m pytest tests/test_state_machine.py -v`
Expected: All existing tests PASS

- [ ] **Step 8: Commit**

```bash
git add src/models.py src/state_machine.py routes/runs.py tests/test_openclaw_hooks.py
git commit -m "feat: store notify_channel/notify_to in create_run API"
```

---

### Task 4: WebhookNotifier — OpenClaw delivery path

**Files:**
- Modify: `src/webhook_notifier.py`
- Modify: `src/app.py:29`
- Test: `tests/test_openclaw_hooks.py`

- [ ] **Step 1: Write the failing tests**

Append to `tests/test_openclaw_hooks.py`:

```python
import json
from unittest.mock import patch, MagicMock
from src.webhook_notifier import WebhookNotifier
from src.config import OpenclawHooksConfig


@pytest.fixture
async def wn_with_hooks(db):
    """WebhookNotifier with OpenClaw hooks enabled."""
    hooks_cfg = OpenclawHooksConfig(
        enabled=True,
        url="http://localhost:18789/hooks/agent",
        token="test-token",
        default_channel="feishu",
        default_to="ou_default",
    )
    n = WebhookNotifier(db, openclaw_hooks=hooks_cfg)
    yield n
    await n.close()


@pytest.fixture
async def wn_no_hooks(db):
    """WebhookNotifier with OpenClaw hooks disabled."""
    n = WebhookNotifier(db)
    yield n
    await n.close()


async def test_openclaw_delivery_called_for_filtered_event(wn_with_hooks):
    """OpenClaw delivery should fire for events in the allowed set."""
    wn_with_hooks._deliver_to_openclaw = AsyncMock()
    wn_with_hooks._deliver_with_retry = AsyncMock()
    await wn_with_hooks.notify("gate.waiting", {"run_id": "r1"})
    wn_with_hooks._deliver_to_openclaw.assert_called_once()


async def test_openclaw_delivery_skipped_for_unfiltered_event(wn_with_hooks):
    """OpenClaw delivery should NOT fire for events outside the allowed set."""
    wn_with_hooks._deliver_to_openclaw = AsyncMock()
    wn_with_hooks._deliver_with_retry = AsyncMock()
    await wn_with_hooks.notify("stage.changed", {"run_id": "r1"})
    wn_with_hooks._deliver_to_openclaw.assert_not_called()


async def test_openclaw_delivery_skipped_when_disabled(wn_no_hooks):
    """When hooks config is not provided, OpenClaw delivery should not happen."""
    wn_no_hooks._deliver_to_openclaw = AsyncMock()
    wn_no_hooks._deliver_with_retry = AsyncMock()
    await wn_no_hooks.notify("gate.waiting", {"run_id": "r1"})
    wn_no_hooks._deliver_to_openclaw.assert_not_called()


async def test_openclaw_message_format(wn_with_hooks, db):
    """The OpenClaw message should follow [cooagents:{event}] format."""
    # Insert a run row so _deliver_to_openclaw can look up ticket/stage
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,"
        "notify_channel,notify_to,created_at,updated_at) VALUES(?,?,?,?,?,?,?,?,?)",
        ("r1", "PROJ-42", "/repo", "running", "DESIGN_REVIEW",
         "feishu", "ou_user1", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )

    captured = {}

    async def mock_post(url, content, headers):
        captured["url"] = url
        captured["body"] = json.loads(content)
        captured["headers"] = headers
        resp = MagicMock()
        resp.status_code = 200
        return resp

    mock_client = AsyncMock()
    mock_client.post = mock_post
    wn_with_hooks._client = mock_client

    await wn_with_hooks._deliver_to_openclaw("gate.waiting", {"run_id": "r1"})

    assert captured["url"] == "http://localhost:18789/hooks/agent"
    assert "Authorization" in captured["headers"]
    assert captured["headers"]["Authorization"] == "Bearer test-token"
    body = captured["body"]
    assert "[cooagents:gate.waiting]" in body["message"]
    assert body["channel"] == "feishu"
    assert body["to"] == "ou_user1"
    assert body["deliver"] is True
    assert "idempotencyKey" in body


async def test_openclaw_uses_global_defaults_when_run_has_no_notify(wn_with_hooks, db):
    """When run has no notify_channel/notify_to, use global defaults from config."""
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r2", "PROJ-99", "/repo", "running", "DEV_REVIEW",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )

    captured = {}

    async def mock_post(url, content, headers):
        captured["body"] = json.loads(content)
        resp = MagicMock()
        resp.status_code = 200
        return resp

    mock_client = AsyncMock()
    mock_client.post = mock_post
    wn_with_hooks._client = mock_client

    await wn_with_hooks._deliver_to_openclaw("job.completed", {"run_id": "r2"})

    assert captured["body"]["channel"] == "feishu"
    assert captured["body"]["to"] == "ou_default"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_openclaw_hooks.py -k "openclaw_delivery or openclaw_message or openclaw_uses_global" -v`
Expected: FAIL — WebhookNotifier doesn't accept `openclaw_hooks` param

- [ ] **Step 3: Implement OpenClaw delivery in WebhookNotifier**

Rewrite `src/webhook_notifier.py`:

```python
import json
import hmac
import hashlib
import asyncio
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
})


class WebhookNotifier:
    def __init__(self, db, openclaw_hooks=None):
        self.db = db
        self._client = None
        self._openclaw_hooks = openclaw_hooks

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
        """POST event to OpenClaw /hooks/agent endpoint."""
        cfg = self._openclaw_hooks
        run_id = payload.get("run_id")

        # Look up run for ticket, stage, and per-run notify config
        ticket = ""
        stage = ""
        channel = cfg.default_channel
        to = cfg.default_to

        if run_id:
            run = await self.db.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
            if run:
                ticket = run.get("ticket", "")
                stage = run.get("current_stage", "")
                channel = run.get("notify_channel") or cfg.default_channel
                to = run.get("notify_to") or cfg.default_to

        # Format structured message
        message = (
            f"[cooagents:{event_type}] {ticket} {stage}\n"
            f"run_id: {run_id or 'unknown'}\n"
            f"ticket: {ticket}\n"
            f"stage: {stage}"
        )

        # Idempotency key: cooagents:{run_id}:{event_type}:{timestamp_s}
        ts = int(datetime.now(timezone.utc).timestamp())
        idem_key = f"cooagents:{run_id or 'system'}:{event_type}:{ts}"
        if len(idem_key) > 256:
            idem_key = idem_key[:256]

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
                now = datetime.now(timezone.utc).isoformat()
                await self.db.execute(
                    "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                    (run_id or "system", "openclaw.hooks.delivery_failed",
                     json.dumps({"event_type": event_type, "status_code": resp.status_code}), now)
                )
        except Exception:
            now = datetime.now(timezone.utc).isoformat()
            await self.db.execute(
                "INSERT INTO events(run_id,event_type,payload_json,created_at) VALUES(?,?,?,?)",
                (run_id or "system", "openclaw.hooks.delivery_failed",
                 json.dumps({"event_type": event_type, "error": "connection_error"}), now)
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
```

- [ ] **Step 4: Wire config in app.py**

In `src/app.py`, change the WebhookNotifier initialization to pass hooks config:

```python
webhooks = WebhookNotifier(db, openclaw_hooks=settings.openclaw.hooks if settings.openclaw.hooks.enabled else None)
```

Note: Since existing code creates `WebhookNotifier(db)` without the new param, backward compatibility is preserved — the default is `None` (disabled).

- [ ] **Step 5: Run all OpenClaw hooks tests**

Run: `python -m pytest tests/test_openclaw_hooks.py -v`
Expected: All PASS

- [ ] **Step 6: Run existing webhook notifier tests to verify no regression**

Run: `python -m pytest tests/test_webhook_notifier.py -v`
Expected: All PASS

- [ ] **Step 7: Commit**

```bash
git add src/webhook_notifier.py src/app.py tests/test_openclaw_hooks.py
git commit -m "feat: add OpenClaw /hooks/agent delivery path to WebhookNotifier"
```

---

### Task 5: SKILL.md — Add webhook event and approval reply sections

**Files:**
- Modify: `skills/cooagents-workflow/SKILL.md:83-99`

- [ ] **Step 1: Add webhook event message format section**

After section D (Webhook 事件处理) at line ~99, add section G:

```markdown
## G. Webhook 事件消息（隔离会话）

你会通过 hooks 收到格式如下的事件通知：

```
[cooagents:{event_type}] {ticket} {stage}
run_id: {run_id}
ticket: {ticket}
stage: {current_stage}
```

收到后按上方决策树（§B）中对应阶段的动作执行。

注意：你在隔离会话中运行，处理完即结束。你的回复会通过 deliver 机制自动投递到用户的消息渠道。

对于审批类事件（`gate.waiting`）：
1. exec `curl GET /api/v1/runs/{run_id}/artifacts` 获取产物内容
2. 使用 `references/feishu-interaction.md` 中的模板格式化审批请求
3. 回复审批模板（会自动投递到用户）
4. 你不需要等待用户回复 — 用户的回复会由主会话 Agent 处理

对于通知类事件（`run.completed`、`merge.conflict` 等）：
1. 格式化通知消息
2. 回复通知（会自动投递到用户）
```

- [ ] **Step 2: Add approval reply handling section**

Add section H:

```markdown
## H. 审批回复处理（主会话）

当用户在对话中回复审批相关内容时（如"通过"、"驳回：原因..."），参考聊天记录中的审批请求消息，识别对应的 ticket 和 gate，然后执行审批操作。

示例场景：
- 聊天记录中有 "📋 任务 PROJ-42 等待审批 (design)"
- 用户回复 "通过"
- 你应执行：
  1. exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/approve -H "Content-Type: application/json" -d '{"gate":"design","by":"用户标识"}'`
  2. exec `curl -s -X POST http://127.0.0.1:8321/api/v1/runs/{run_id}/tick`
```

- [ ] **Step 3: Verify SKILL.md is valid markdown**

Read the file and verify sections are correctly structured and no duplicate section letters.

- [ ] **Step 4: Commit**

```bash
git add skills/cooagents-workflow/SKILL.md
git commit -m "feat: add webhook event and approval reply sections to SKILL.md"
```

---

### Task 6: Integration verification

**Files:**
- Test: `tests/test_openclaw_hooks.py`
- Test: `tests/test_state_machine.py`
- Test: `tests/test_webhook_notifier.py`

- [ ] **Step 1: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: All tests PASS, no regressions

- [ ] **Step 2: Verify event filter completeness**

Verify that the `OPENCLAW_EVENTS` set matches the spec table (Section 4.5 of the spec):
- `gate.waiting`, `job.completed`, `job.failed`, `job.timeout`, `job.interrupted`
- `merge.conflict`, `merge.completed`, `run.completed`, `run.cancelled`, `host.online`

- [ ] **Step 3: Commit any fixes if needed**

Only if previous steps revealed issues.
