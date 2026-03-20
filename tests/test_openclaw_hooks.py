import json

import pytest
from unittest.mock import AsyncMock, MagicMock, patch

from src.database import Database
from src.config import OpenclawHooksConfig, Settings
from src.artifact_manager import ArtifactManager
from src.state_machine import StateMachine
from src.webhook_notifier import WebhookNotifier, OPENCLAW_EVENTS


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()


@pytest.fixture
async def sm(db, tmp_path):
    (tmp_path / ".git").mkdir(exist_ok=True)
    am = ArtifactManager(db)
    am.render_task = AsyncMock(return_value="task-path")
    webhook = AsyncMock()
    webhook.notify = AsyncMock()
    executor = AsyncMock()
    host_mgr = AsyncMock()
    merge_mgr = AsyncMock()
    return StateMachine(db, am, host_mgr, executor, webhook, merge_mgr)


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


# ---------------------------------------------------------------------------
# Task 1: Schema — notify columns
# ---------------------------------------------------------------------------

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


# ---------------------------------------------------------------------------
# Task 2: Config — OpenclawHooksConfig
# ---------------------------------------------------------------------------

def test_openclaw_hooks_config_defaults():
    """OpenclawHooksConfig should have sensible defaults."""
    cfg = OpenclawHooksConfig()
    assert cfg.enabled is False
    assert cfg.url == "http://127.0.0.1:18789/hooks/agent"
    assert cfg.token == ""
    assert cfg.default_channel == "last"
    assert cfg.default_to == ""


def test_settings_has_openclaw_hooks():
    """Settings.openclaw should include a hooks sub-config."""
    s = Settings()
    assert hasattr(s.openclaw, "hooks")
    assert isinstance(s.openclaw.hooks, OpenclawHooksConfig)


# ---------------------------------------------------------------------------
# Task 3: State Machine — notify fields on create_run
# ---------------------------------------------------------------------------

async def test_create_run_stores_notify_fields(sm, db, tmp_path):
    """create_run should persist notify_channel and notify_to."""
    run = await sm.create_run(
        "T-1", str(tmp_path),
        notify_channel="feishu", notify_to="ou_abc123",
    )
    row = await db.fetchone("SELECT notify_channel, notify_to FROM runs WHERE id=?", (run["id"],))
    assert row["notify_channel"] == "feishu"
    assert row["notify_to"] == "ou_abc123"


async def test_create_run_notify_fields_optional(sm, db, tmp_path):
    """create_run without notify fields should store NULL."""
    run = await sm.create_run("T-2", str(tmp_path))
    row = await db.fetchone("SELECT notify_channel, notify_to FROM runs WHERE id=?", (run["id"],))
    assert row["notify_channel"] is None
    assert row["notify_to"] is None


# ---------------------------------------------------------------------------
# Task 4: WebhookNotifier — OpenClaw delivery
# ---------------------------------------------------------------------------

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


async def test_openclaw_prefers_event_stage_over_run_current_stage(wn_with_hooks, db):
    """Timeout-like events should display the stage carried by the event payload."""
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r-stage", "PROJ-STAGE", "/repo", "running", "DESIGN_DISPATCHED", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
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

    await wn_with_hooks._deliver_to_openclaw(
        "job.timeout",
        {"run_id": "r-stage", "job_id": "job-1", "stage": "DESIGN_QUEUED"},
    )

    assert "DESIGN_QUEUED" in captured["body"]["message"]
    assert "DESIGN_DISPATCHED" not in captured["body"]["message"]


async def test_openclaw_prefers_current_stage_over_job_stage(wn_with_hooks, db):
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r-current-stage", "PROJ-CUR", "/repo", "running", "DESIGN_RUNNING", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
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

    await wn_with_hooks._deliver_to_openclaw(
        "job.timeout",
        {
            "run_id": "r-current-stage",
            "job_id": "job-2",
            "job_stage": "DESIGN_RUNNING",
            "current_stage": "DESIGN_REVIEW",
        },
    )

    assert "DESIGN_REVIEW" in captured["body"]["message"]
    assert "DESIGN_RUNNING" not in captured["body"]["message"]


async def test_openclaw_delivery_retries_before_succeeding(wn_with_hooks, db):
    """OpenClaw delivery should retry transient failures before logging failure."""
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r3", "PROJ-100", "/repo", "running", "REQ_REVIEW",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )

    attempts = {"count": 0}

    async def mock_post(url, content, headers):
        attempts["count"] += 1
        if attempts["count"] < 3:
            raise RuntimeError("temporary network failure")
        resp = MagicMock()
        resp.status_code = 200
        return resp

    mock_client = AsyncMock()
    mock_client.post = mock_post
    wn_with_hooks._client = mock_client

    with patch("src.webhook_notifier.asyncio.sleep", new=AsyncMock()):
        await wn_with_hooks.notify("gate.waiting", {"run_id": "r3"})

    rows = await db.fetchall(
        "SELECT * FROM events WHERE event_type='openclaw.hooks.delivery_failed'"
    )
    assert attempts["count"] == 3
    assert rows == []


async def test_openclaw_idempotency_key_changes_even_with_same_second(wn_with_hooks, db):
    """OpenClaw idempotencyKey should stay unique for repeated events in the same second."""
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,"
        "created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r4", "PROJ-101", "/repo", "running", "REQ_REVIEW",
         "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z"),
    )

    captured = []

    async def mock_post(url, content, headers):
        captured.append(json.loads(content))
        resp = MagicMock()
        resp.status_code = 200
        return resp

    class FixedMoment:
        def isoformat(self):
            return "2026-03-18T01:02:03+00:00"

        def timestamp(self):
            return 1_774_333_323

    class FixedDateTime:
        @classmethod
        def now(cls, tz=None):
            return FixedMoment()

    mock_client = AsyncMock()
    mock_client.post = mock_post
    wn_with_hooks._client = mock_client

    with patch("src.webhook_notifier.datetime", FixedDateTime):
        await wn_with_hooks._deliver_to_openclaw("gate.waiting", {"run_id": "r4"})
        await wn_with_hooks._deliver_to_openclaw("gate.waiting", {"run_id": "r4"})

    assert len(captured) == 2
    assert captured[0]["idempotencyKey"] != captured[1]["idempotencyKey"]


# ---------------------------------------------------------------------------
# Task 6: Event filter completeness
# ---------------------------------------------------------------------------

def test_openclaw_events_completeness():
    """OPENCLAW_EVENTS should match the spec's event list."""
    expected = {
        "gate.waiting", "job.completed", "job.failed", "job.timeout",
        "job.interrupted", "merge.conflict", "merge.completed",
        "run.completed", "run.cancelled", "host.online", "host.unavailable",
    }
    assert OPENCLAW_EVENTS == expected


# ---------------------------------------------------------------------------
# gate.waiting emission
# ---------------------------------------------------------------------------

async def test_gate_waiting_emitted_on_review_stage(sm, db, tmp_path):
    """Entering a *_REVIEW stage should emit gate.waiting event."""
    run = await sm.create_run("T-GW", str(tmp_path))
    run_id = run["id"]

    # Submit requirement to advance to REQ_REVIEW
    await sm.submit_requirement(run_id, "test requirement")

    # Check that gate.waiting was emitted for req gate
    rows = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='gate.waiting'", (run_id,)
    )
    assert len(rows) == 1
    import json
    payload = json.loads(rows[0]["payload_json"])
    assert payload["gate"] == "req"
    assert payload["stage"] == "REQ_REVIEW"


async def test_gate_waiting_emitted_on_approve_to_next_review(sm, db, tmp_path):
    """Approving req gate → DESIGN flow → eventually DESIGN_REVIEW should emit gate.waiting."""
    run = await sm.create_run("T-GW2", str(tmp_path))
    run_id = run["id"]

    # Advance to REQ_REVIEW
    await sm.submit_requirement(run_id, "req content")

    # Approve req → should go to DESIGN_QUEUED (no gate.waiting for DESIGN_QUEUED)
    await sm.approve(run_id, "req", "tester")
    updated = await db.fetchone("SELECT current_stage FROM runs WHERE id=?", (run_id,))
    assert updated["current_stage"] == "DESIGN_QUEUED"

    # gate.waiting should only have been emitted for REQ_REVIEW, not DESIGN_QUEUED
    rows = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='gate.waiting'", (run_id,)
    )
    assert len(rows) == 1  # only for REQ_REVIEW


async def test_no_gate_waiting_for_auto_stages(sm, db, tmp_path):
    """Auto stages (INIT, *_QUEUED, *_DISPATCHED, etc.) should NOT emit gate.waiting."""
    run = await sm.create_run("T-NGW", str(tmp_path))
    run_id = run["id"]

    # create_run goes INIT → REQ_COLLECTING, neither should emit gate.waiting
    rows = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='gate.waiting'", (run_id,)
    )
    assert len(rows) == 0


# ---------------------------------------------------------------------------
# host.unavailable emission
# ---------------------------------------------------------------------------

async def test_host_unavailable_emitted_on_design_queued_no_hosts(sm, db, tmp_path):
    """Ticking DESIGN_QUEUED with no hosts should emit host.unavailable."""
    run = await sm.create_run("T-HU", str(tmp_path))
    run_id = run["id"]

    await sm.submit_requirement(run_id, "req content")
    await sm.approve(run_id, "req", "tester")
    # Simulate no available hosts
    sm.hosts.select_host = AsyncMock(return_value=None)
    # Now at DESIGN_QUEUED with no hosts registered
    await sm.tick(run_id)

    rows = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='host.unavailable'", (run_id,)
    )
    assert len(rows) >= 1
    import json
    payload = json.loads(rows[0]["payload_json"])
    assert payload["stage"] == "DESIGN_QUEUED"
    assert payload["agent_type"] == "claude"

    # Run should still be in DESIGN_QUEUED (not advanced)
    updated = await db.fetchone("SELECT current_stage FROM runs WHERE id=?", (run_id,))
    assert updated["current_stage"] == "DESIGN_QUEUED"
