# Agent Preference & Fallback Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Allow users to choose preferred agent (claude/codex) for design and dev phases, with automatic fallback and host CLI validation.

**Architecture:** Add `preferred_design_agent` / `preferred_dev_agent` to config, optional per-run overrides in `CreateRunRequest`, fallback dispatch logic in the state machine, and CLI-aware health checks in `HostManager`.

**Tech Stack:** Python, Pydantic, SQLite (aiosqlite), FastAPI, pytest

---

### Task 1: Add config fields for preferred agents

**Files:**
- Modify: `src/config.py:87-96` (Settings model)
- Modify: `config/settings.yaml:33` (after turns section)
- Test: `tests/test_config.py`

- [ ] **Step 1: Write failing test for new config fields**

In `tests/test_config.py`, add:

```python
def test_agent_preference_config_defaults():
    s = Settings()
    assert s.preferred_design_agent == "claude"
    assert s.preferred_dev_agent == "claude"

def test_agent_preference_config_from_dict():
    s = Settings.model_validate({
        "preferred_design_agent": "codex",
        "preferred_dev_agent": "codex",
    })
    assert s.preferred_design_agent == "codex"
    assert s.preferred_dev_agent == "codex"
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_config.py::test_agent_preference_config_defaults tests/test_config.py::test_agent_preference_config_from_dict -v`
Expected: FAIL — `Settings` has no attribute `preferred_design_agent`

- [ ] **Step 3: Add fields to Settings model**

In `src/config.py`, add two fields to the `Settings` class (after `tracing`):

```python
class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    timeouts: TimeoutConfig = TimeoutConfig()
    health_check: HealthCheckConfig = HealthCheckConfig()
    merge: MergeConfig = MergeConfig()
    acpx: AcpxConfig = AcpxConfig()
    turns: TurnsConfig = TurnsConfig()
    openclaw: OpenclawConfig = OpenclawConfig()
    tracing: TracingConfig = TracingConfig()
    preferred_design_agent: str = "claude"
    preferred_dev_agent: str = "claude"
```

- [ ] **Step 4: Add fields to settings.yaml**

Append after the `turns` section in `config/settings.yaml`:

```yaml
preferred_design_agent: "claude"
preferred_dev_agent: "claude"
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/config.py config/settings.yaml tests/test_config.py
git commit -m "feat: add preferred_design_agent and preferred_dev_agent config fields"
```

---

### Task 2: Add design_agent/dev_agent to CreateRunRequest and DB schema

**Files:**
- Modify: `src/models.py:26-33` (CreateRunRequest)
- Modify: `db/schema.sql:4-22` (runs table)
- Modify: `src/database.py:72-99` (_apply_compat_migrations)
- Test: `tests/test_database.py`

- [ ] **Step 1: Write failing test for migration**

In `tests/test_database.py`, add:

```python
async def test_runs_has_agent_columns(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    row = await d.fetchone("PRAGMA table_info(runs)")
    cols = []
    async with d._ensure_connected().execute("PRAGMA table_info(runs)") as cursor:
        rows = await cursor.fetchall()
    cols = [r["name"] for r in rows]
    assert "design_agent" in cols
    assert "dev_agent" in cols
    await d.close()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_database.py::test_runs_has_agent_columns -v`
Expected: FAIL — columns do not exist

- [ ] **Step 3: Add columns to schema.sql**

In `db/schema.sql`, add two columns to the `runs` table (after `notify_to`):

```sql
CREATE TABLE IF NOT EXISTS runs (
  id              TEXT PRIMARY KEY,
  ticket          TEXT NOT NULL,
  repo_path       TEXT NOT NULL,
  repo_url        TEXT,
  status          TEXT DEFAULT 'running' CHECK(status IN ('running','completed','failed','cancelled')),
  current_stage   TEXT NOT NULL DEFAULT 'INIT',
  description     TEXT,
  failed_at_stage TEXT,
  design_worktree TEXT,
  design_branch   TEXT,
  dev_worktree    TEXT,
  dev_branch      TEXT,
  preferences_json TEXT,
  notify_channel  TEXT,
  notify_to       TEXT,
  design_agent    TEXT DEFAULT 'claude' CHECK(design_agent IN ('claude','codex')),
  dev_agent       TEXT DEFAULT 'claude' CHECK(dev_agent IN ('claude','codex')),
  created_at      TEXT NOT NULL,
  updated_at      TEXT NOT NULL
);
```

- [ ] **Step 4: Add compat migration for existing databases**

In `src/database.py`, add at the end of `_apply_compat_migrations()`:

```python
        # Agent preference columns on runs
        if not await self._column_exists("runs", "design_agent"):
            await conn.execute("ALTER TABLE runs ADD COLUMN design_agent TEXT DEFAULT 'claude'")
        if not await self._column_exists("runs", "dev_agent"):
            await conn.execute("ALTER TABLE runs ADD COLUMN dev_agent TEXT DEFAULT 'claude'")
```

- [ ] **Step 5: Add fields to CreateRunRequest**

In `src/models.py`, add to `CreateRunRequest`:

```python
class CreateRunRequest(BaseModel):
    ticket: str
    repo_path: str
    description: str | None = None
    preferences: dict | None = None
    notify_channel: str | None = None
    notify_to: str | None = None
    repo_url: str | None = None
    design_agent: str | None = None
    dev_agent: str | None = None
```

- [ ] **Step 6: Run tests to verify they pass**

Run: `python -m pytest tests/test_database.py -v`
Expected: ALL PASS

- [ ] **Step 7: Commit**

```bash
git add db/schema.sql src/database.py src/models.py
git commit -m "feat: add design_agent and dev_agent to runs table and CreateRunRequest"
```

---

### Task 3: Wire create_run() and route to accept agent preferences

**Files:**
- Modify: `src/state_machine.py:121-165` (create_run)
- Modify: `routes/runs.py:12-20` (create_run route)
- Test: `tests/test_state_machine.py`

- [ ] **Step 1: Write failing test**

In `tests/test_state_machine.py`, add:

```python
async def test_create_run_stores_agent_preferences(sm, tmp_path):
    run = await sm.create_run("T-PREF", str(tmp_path), design_agent="codex", dev_agent="codex")
    assert run["design_agent"] == "codex"
    assert run["dev_agent"] == "codex"

async def test_create_run_defaults_agent_from_config(sm, tmp_path):
    run = await sm.create_run("T-DEF", str(tmp_path))
    assert run["design_agent"] == "claude"
    assert run["dev_agent"] == "claude"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_state_machine.py::test_create_run_stores_agent_preferences tests/test_state_machine.py::test_create_run_defaults_agent_from_config -v`
Expected: FAIL — `create_run()` does not accept `design_agent`

- [ ] **Step 3: Update create_run() in state_machine.py**

Modify `create_run()` signature and body:

```python
    async def create_run(
        self,
        ticket: str,
        repo_path: str,
        description: str | None = None,
        preferences: dict | None = None,
        notify_channel: str | None = None,
        notify_to: str | None = None,
        repo_url: str | None = None,
        design_agent: str | None = None,
        dev_agent: str | None = None,
    ) -> dict:
```

Resolve defaults from config:

```python
        # Resolve agent preferences
        if design_agent is None:
            design_agent = getattr(self._config, "preferred_design_agent", "claude") if self._config else "claude"
        if dev_agent is None:
            dev_agent = getattr(self._config, "preferred_dev_agent", "claude") if self._config else "claude"
```

Update the INSERT to include the new columns:

```python
        await self.db.execute(
            "INSERT INTO runs(id,ticket,repo_path,repo_url,status,current_stage,"
            "description,preferences_json,notify_channel,notify_to,"
            "design_agent,dev_agent,created_at,updated_at) "
            "VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
            (run_id, ticket, repo_path, repo_url, "running", "INIT", description, prefs,
             notify_channel, notify_to, design_agent, dev_agent, now, now),
        )
```

- [ ] **Step 4: Update the route**

In `routes/runs.py`, pass the new fields:

```python
@router.post("/runs", status_code=201)
async def create_run(req: CreateRunRequest, request: Request):
    sm = request.app.state.sm
    result = await sm.create_run(
        req.ticket, req.repo_path, req.description, req.preferences,
        notify_channel=req.notify_channel, notify_to=req.notify_to,
        repo_url=req.repo_url,
        design_agent=req.design_agent, dev_agent=req.dev_agent,
    )
    return result
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_state_machine.py::test_create_run_stores_agent_preferences tests/test_state_machine.py::test_create_run_defaults_agent_from_config tests/test_state_machine.py::test_create_run -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/state_machine.py routes/runs.py tests/test_state_machine.py
git commit -m "feat: wire create_run and route to accept design_agent/dev_agent"
```

---

### Task 4: Implement fallback dispatch logic for design phase

**Files:**
- Modify: `src/state_machine.py:454-525` (_tick_design_queued)
- Modify: `src/state_machine.py:544-588` (_tick_design_running)
- Test: `tests/test_state_machine.py`

- [ ] **Step 1: Write failing test — fallback dispatch**

In `tests/test_state_machine.py`, add:

```python
async def test_design_queued_falls_back_to_codex(sm, mocks, db, tmp_path):
    """When preferred design agent (claude) has no host, fall back to codex."""
    _, _, host_mgr, _ = mocks

    # First call (claude) returns None, second call (codex) returns a host
    host_mgr.select_host = AsyncMock(side_effect=[None, {"id": "local", "host": "local"}])

    run = await sm.create_run("T-FB-D", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Req")
    await sm.approve(run["run_id"], "req", "user1")
    run = await sm.tick(run["run_id"])
    assert run["current_stage"] == "DESIGN_DISPATCHED"

    # Verify fallback event was emitted
    events = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='agent.fallback'",
        (run["run_id"],),
    )
    assert len(events) == 1

async def test_design_queued_no_host_at_all(sm, mocks, tmp_path):
    """When neither preferred nor fallback agent has a host, stay in DESIGN_QUEUED."""
    _, _, host_mgr, _ = mocks
    host_mgr.select_host = AsyncMock(return_value=None)

    run = await sm.create_run("T-NOHOST-D", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Req")
    await sm.approve(run["run_id"], "req", "user1")
    run = await sm.tick(run["run_id"])
    assert run["current_stage"] == "DESIGN_QUEUED"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_state_machine.py::test_design_queued_falls_back_to_codex tests/test_state_machine.py::test_design_queued_no_host_at_all -v`
Expected: FAIL — current code doesn't try fallback

- [ ] **Step 3: Implement fallback in _tick_design_queued**

Replace the host selection block in `_tick_design_queued()` (lines 464-471):

```python
            preferred = run.get("design_agent") or "claude"
            fallback = "codex" if preferred == "claude" else "claude"

            host = await self.hosts.select_host(preferred)
            actual_agent = preferred
            if not host:
                host = await self.hosts.select_host(fallback)
                actual_agent = fallback
            if not host:
                await self._emit_limited(run["id"], "host.unavailable", {
                    "stage": "DESIGN_QUEUED",
                    "agent_type": preferred,
                    "ticket": run["ticket"],
                }, limit_keys=("stage",))
                return
            if actual_agent != preferred:
                await self._emit(run["id"], "agent.fallback", {
                    "stage": "DESIGN_QUEUED",
                    "preferred": preferred,
                    "actual": actual_agent,
                    "ticket": run["ticket"],
                })
```

Then replace all hardcoded `"claude"` in the dispatch call with `actual_agent`:

```python
            if hasattr(self.executor, 'start_session'):
                await self.executor.start_session(run["id"], host, actual_agent, task_path, wt, timeout_sec)
            else:
                await self.executor.dispatch(run["id"], host, actual_agent, task_path, wt, timeout_sec)
```

- [ ] **Step 4: Update _tick_design_running to use job's agent_type**

In `_tick_design_running()`, replace hardcoded `"claude"` in `close_session` and `send_followup` calls. Read agent_type from the job record:

```python
        job_agent = job.get("agent_type", "claude")
```

Replace `close_session(run["id"], "claude")` with:
```python
                await self.executor.close_session(run["id"], job_agent)
```

Replace `send_followup(run["id"], "claude", ...)` with:
```python
                await self.executor.send_followup(
                    run["id"], job_agent, revision_path, wt, self._execution_timeout("design")
                )
```

Replace `"agent_type": "claude"` in `turn.started` event with:
```python
                await self._emit(run["id"], "turn.started", {"turn_num": turn + 1, "agent_type": job_agent})
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_state_machine.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/state_machine.py tests/test_state_machine.py
git commit -m "feat: implement fallback dispatch logic for design phase"
```

---

### Task 5: Implement fallback dispatch logic for dev phase

**Files:**
- Modify: `src/state_machine.py:590-728` (_tick_dev_queued, _tick_dev_running)
- Test: `tests/test_state_machine.py`

- [ ] **Step 1: Write failing test — dev fallback**

In `tests/test_state_machine.py`, add:

```python
async def test_dev_queued_falls_back_to_claude(sm, mocks, db, tmp_path):
    """When preferred dev agent has no host, fall back to the other."""
    _, _, host_mgr, _ = mocks

    # Design phase works normally (returns host for claude)
    # Dev phase: first call (claude — the new default) returns None, second (codex) succeeds
    call_count = 0
    async def _select_host_side_effect(agent_type, preferred_host=None):
        nonlocal call_count
        call_count += 1
        # First 1 call is design phase (claude) — return host
        if call_count <= 1:
            return {"id": "local", "host": "local"}
        # Dev phase: preferred (claude) fails, fallback (codex) succeeds
        if agent_type == "codex":
            return {"id": "local", "host": "local"}
        return None

    host_mgr.select_host = AsyncMock(side_effect=_select_host_side_effect)

    run = await sm.create_run("T-FB-DEV", str(tmp_path))
    await sm.submit_requirement(run["run_id"], "# Req")
    await sm.approve(run["run_id"], "req", "user1")

    # Tick through design
    await sm.tick(run["run_id"])  # DESIGN_QUEUED -> DESIGN_DISPATCHED

    # Simulate design completion
    job = await db.fetchone("SELECT * FROM jobs WHERE run_id=? ORDER BY started_at DESC LIMIT 1", (run["run_id"],))
    if job:
        await db.execute("UPDATE jobs SET status='completed' WHERE id=?", (job["id"],))

    # Advance through design stages to DEV_QUEUED
    await sm.approve(run["run_id"], "design", "user1")
    run_state = await db.fetchone("SELECT * FROM runs WHERE id=?", (run["run_id"],))

    # Tick dev
    run = await sm.tick(run["run_id"])

    events = await db.fetchall(
        "SELECT * FROM events WHERE run_id=? AND event_type='agent.fallback'",
        (run["run_id"],),
    )
    assert len(events) >= 1
```

- [ ] **Step 2: Run test to verify it fails**

Run: `python -m pytest tests/test_state_machine.py::test_dev_queued_falls_back_to_claude -v`
Expected: FAIL

- [ ] **Step 3: Implement fallback in _tick_dev_queued**

Replace the host selection block in `_tick_dev_queued()` (lines 600-607):

```python
            preferred = run.get("dev_agent") or "claude"
            fallback = "codex" if preferred == "claude" else "claude"

            host = await self.hosts.select_host(preferred)
            actual_agent = preferred
            if not host:
                host = await self.hosts.select_host(fallback)
                actual_agent = fallback
            if not host:
                await self._emit_limited(run["id"], "host.unavailable", {
                    "stage": "DEV_QUEUED",
                    "agent_type": preferred,
                    "ticket": run["ticket"],
                }, limit_keys=("stage",))
                return
            if actual_agent != preferred:
                await self._emit(run["id"], "agent.fallback", {
                    "stage": "DEV_QUEUED",
                    "preferred": preferred,
                    "actual": actual_agent,
                    "ticket": run["ticket"],
                })
```

Replace hardcoded `"codex"` in dispatch calls with `actual_agent`:

```python
            if hasattr(self.executor, 'start_session'):
                await self.executor.start_session(run["id"], host, actual_agent, task_path, wt, timeout_sec)
            else:
                await self.executor.dispatch(run["id"], host, actual_agent, task_path, wt, timeout_sec)
```

- [ ] **Step 4: Update _tick_dev_running to use job's agent_type**

In `_tick_dev_running()`, replace hardcoded `"codex"` references. Read from job:

```python
        job_agent = job.get("agent_type", "codex")
```

Replace `close_session(run["id"], "codex")` with:
```python
                await self.executor.close_session(run["id"], job_agent)
```

Replace `send_followup(run["id"], "codex", ...)` with:
```python
                await self.executor.send_followup(
                    run["id"], job_agent, revision_path, wt, self._execution_timeout("dev")
                )
```

Replace `"agent_type": "codex"` in `turn.started` event with:
```python
                await self._emit(run["id"], "turn.started", {"turn_num": turn + 1, "agent_type": job_agent})
```

- [ ] **Step 5: Run tests**

Run: `python -m pytest tests/test_state_machine.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add src/state_machine.py tests/test_state_machine.py
git commit -m "feat: implement fallback dispatch logic for dev phase"
```

---

### Task 6: Enhance host health check with CLI validation

**Files:**
- Modify: `src/host_manager.py:92-116` (health_check)
- Test: `tests/test_host_manager.py`

- [ ] **Step 1: Write failing tests**

In `tests/test_host_manager.py`, add:

```python
async def test_health_check_validates_claude_cli(hm, db):
    """Host with agent_type='claude' must have claude CLI available."""
    await hm.register("h-claude", "local", "claude")
    import unittest.mock
    with unittest.mock.patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: "/usr/bin/codex" if cmd == "codex" else ("/usr/bin/acpx" if cmd == "acpx" else None)
        result = await hm.health_check("h-claude")
    assert result is False
    host = await db.fetchone("SELECT * FROM agent_hosts WHERE id='h-claude'")
    assert host["status"] == "offline"

async def test_health_check_validates_codex_cli(hm, db):
    """Host with agent_type='codex' must have codex CLI available."""
    await hm.register("h-codex", "local", "codex")
    import unittest.mock
    with unittest.mock.patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: "/usr/bin/claude" if cmd == "claude" else ("/usr/bin/acpx" if cmd == "acpx" else None)
        result = await hm.health_check("h-codex")
    assert result is False
    host = await db.fetchone("SELECT * FROM agent_hosts WHERE id='h-codex'")
    assert host["status"] == "offline"

async def test_health_check_validates_both_cli(hm, db):
    """Host with agent_type='both' must have both CLIs available."""
    await hm.register("h-both", "local", "both")
    import unittest.mock
    with unittest.mock.patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: "/usr/bin/claude" if cmd == "claude" else ("/usr/bin/acpx" if cmd == "acpx" else None)
        result = await hm.health_check("h-both")
    assert result is False

async def test_health_check_passes_when_cli_available(hm, db):
    """Host passes when required CLI is available."""
    await hm.register("h-ok", "local", "claude")
    import unittest.mock
    with unittest.mock.patch("shutil.which") as mock_which:
        mock_which.side_effect = lambda cmd: f"/usr/bin/{cmd}" if cmd in ("acpx", "claude") else None
        result = await hm.health_check("h-ok")
    assert result is True
    host = await db.fetchone("SELECT * FROM agent_hosts WHERE id='h-ok'")
    assert host["status"] == "active"
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_host_manager.py::test_health_check_validates_claude_cli tests/test_host_manager.py::test_health_check_validates_codex_cli tests/test_host_manager.py::test_health_check_validates_both_cli tests/test_host_manager.py::test_health_check_passes_when_cli_available -v`
Expected: FAIL — current health_check doesn't validate per agent_type

- [ ] **Step 3: Implement CLI validation in health_check**

Replace the local host check block in `health_check()` (lines 96-102):

```python
        if host["host"] == "local":
            import shutil
            has_acpx = shutil.which("acpx")
            agent_type = host["agent_type"]
            if has_acpx:
                # acpx wraps both agents, but verify the specific CLI too
                if agent_type == "claude":
                    cli_ok = bool(shutil.which("claude"))
                elif agent_type == "codex":
                    cli_ok = bool(shutil.which("codex"))
                else:  # "both"
                    cli_ok = bool(shutil.which("claude")) and bool(shutil.which("codex"))
            else:
                # No acpx — check direct CLI availability
                if agent_type == "claude":
                    cli_ok = bool(shutil.which("claude"))
                elif agent_type == "codex":
                    cli_ok = bool(shutil.which("codex"))
                else:  # "both"
                    cli_ok = bool(shutil.which("claude")) and bool(shutil.which("codex"))
            status = "active" if cli_ok else "offline"
```

For the remote host block (lines 103-114), update the SSH check:

```python
        else:
            try:
                import asyncssh
                agent_type = host["agent_type"]
                async with asyncssh.connect(
                    host["host"],
                    known_hosts=None,
                    client_keys=[host["ssh_key"]] if host.get("ssh_key") else None,
                ) as conn:
                    if agent_type == "claude":
                        cmds = ["claude"]
                    elif agent_type == "codex":
                        cmds = ["codex"]
                    else:
                        cmds = ["claude", "codex"]
                    cli_ok = True
                    for cmd in cmds:
                        result = await conn.run(f"which {cmd}")
                        if result.returncode != 0:
                            cli_ok = False
                            break
                    status = "active" if cli_ok else "offline"
            except Exception:
                status = "offline"
```

- [ ] **Step 4: Run tests**

Run: `python -m pytest tests/test_host_manager.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/host_manager.py tests/test_host_manager.py
git commit -m "feat: validate agent CLI availability in host health checks"
```

---

### Task 7: Run full test suite and final verification

**Files:**
- All modified files from Tasks 1-6

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: ALL PASS — no regressions

- [ ] **Step 2: Verify no hardcoded agent type strings remain in dispatch paths**

Search for remaining hardcoded agent strings in state_machine.py dispatch/running methods:

```bash
grep -n '"codex"' src/state_machine.py
grep -n '"claude"' src/state_machine.py
```

Verify: No hardcoded `"codex"` or `"claude"` in `_tick_design_queued`, `_tick_design_running`, `_tick_dev_queued`, `_tick_dev_running` (except as fallback defaults in `run.get(...) or "claude"`).

- [ ] **Step 3: Commit any fixes**

If step 2 found issues, fix and commit. Otherwise skip.

- [ ] **Step 4: Final commit — update design spec status**

```bash
git add -A
git commit -m "feat: agent preference and fallback — implementation complete"
```
