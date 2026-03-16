# Workflow API Redesign Implementation Plan

> **For agentic workers:** REQUIRED: Use superpowers:subagent-driven-development (if subagents available) or superpowers:executing-plans to implement this plan. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Refactor cooagents from CLI+cron+tmux architecture to an HTTP API server (FastAPI) with async agent execution, artifact management, and webhook notifications.

**Architecture:** FastAPI server with SQLite (aiosqlite), async agent dispatch via subprocess/asyncssh, webhook callbacks for OpenClaw integration. Single-worker uvicorn process. Modules follow bottom-up dependency: database → models → git_utils → state_machine → agent_executor → routes.

**Tech Stack:** Python 3.11+, FastAPI, uvicorn, aiosqlite, asyncssh, pydantic v2, httpx, pyyaml

**Spec:** `docs/superpowers/specs/2026-03-16-workflow-api-redesign-design.md`

---

## Chunk 1: Foundation (Schema, Config, Database, Models)

### Task 1: Project scaffolding and dependencies

**Files:**
- Create: `requirements.txt`
- Create: `pyproject.toml`
- Create: `src/__init__.py`
- Create: `routes/__init__.py`
- Create: `tests/__init__.py`
- Create: `config/settings.yaml`
- Create: `config/agents.yaml`
- Modify: `.env.example`
- Modify: `.gitignore`

- [ ] **Step 1: Create `pyproject.toml`**

```toml
[project]
name = "cooagents"
version = "0.2.0"
requires-python = ">=3.11"

[tool.pytest.ini_options]
asyncio_mode = "auto"
testpaths = ["tests"]
```

- [ ] **Step 2: Create `requirements.txt`**

```
fastapi>=0.110
uvicorn[standard]>=0.29
aiosqlite>=0.20
asyncssh>=2.14
pyyaml>=6.0
pydantic>=2.0
httpx>=0.27
pytest>=8.0
pytest-asyncio>=0.23
```

- [ ] **Step 3: Create directory structure and `__init__.py` files**

```python
# src/__init__.py — empty
# routes/__init__.py — empty
# tests/__init__.py — empty
```

- [ ] **Step 4: Create `config/settings.yaml`**

```yaml
server:
  host: "127.0.0.1"
  port: 8321

database:
  path: ".coop/state.db"

timeouts:
  dispatch_startup: 300
  design_execution: 1800
  dev_execution: 3600
  review_reminder: 86400

health_check:
  interval: 60
  ssh_timeout: 5

merge:
  auto_rebase: true
  max_resume_count: 3
```

- [ ] **Step 5: Create `config/agents.yaml`**

```yaml
hosts: []
# Example:
# - id: local-pc
#   host: local
#   agent_type: both
#   max_concurrent: 2
# - id: dev-server
#   host: dev@10.0.0.5
#   agent_type: codex
#   max_concurrent: 4
#   ssh_key: ~/.ssh/id_rsa
#   labels: [fast]
```

- [ ] **Step 6: Update `.env.example`**

Add `COOAGENTS_CONFIG_DIR`, `COOAGENTS_COOP_DIR` env var placeholders.

- [ ] **Step 7: Update `.gitignore`**

Add `__pycache__/`, `*.pyc`, `.coop/`, `tasks/*`, `!tasks/.gitkeep`, `*.egg-info/`, `.pytest_cache/`.

- [ ] **Step 8: Install dependencies and verify**

Run: `pip install -r requirements.txt`
Expected: All packages install successfully.

- [ ] **Step 9: Commit**

```bash
git add pyproject.toml requirements.txt src/ routes/ tests/ config/ .env.example .gitignore
git commit -m "feat: scaffold project structure and dependencies for API redesign"
```

---

### Task 2: Database schema

**Files:**
- Modify: `db/schema.sql`

- [ ] **Step 1: Rewrite `db/schema.sql`**

Replace entire file with the 9-table schema from spec Section 12. All tables: `runs`, `steps`, `events`, `approvals`, `webhooks`, `artifacts`, `agent_hosts`, `jobs`, `merge_queue`. Enable WAL mode via `PRAGMA journal_mode=WAL;` at top. Use `CREATE TABLE IF NOT EXISTS` for idempotency.

Key points from spec:
- `runs`: 14 columns including `description`, `failed_at_stage`, `design_worktree`, `design_branch`, `dev_worktree`, `dev_branch`, `preferences_json`
- `approvals`: `UNIQUE(run_id, gate, created_at)` — allows re-approval after rejection
- `jobs`: `status` default `'starting'`, includes `base_commit`, `snapshot_json`, `resume_count`
- `merge_queue`: `status` includes `'skipped'` value

- [ ] **Step 2: Commit**

```bash
git add db/schema.sql
git commit -m "feat: rewrite database schema with 9 tables for API redesign"
```

---

### Task 3: Config loader (`src/config.py`)

**Files:**
- Create: `src/config.py`
- Create: `tests/test_config.py`

- [ ] **Step 1: Write test for config loading**

```python
# tests/test_config.py
import pytest
from src.config import load_settings, load_agent_hosts, Settings

def test_load_settings_defaults():
    settings = load_settings()
    assert settings.server.host == "127.0.0.1"
    assert settings.server.port == 8321
    assert settings.timeouts.dispatch_startup == 300

def test_load_settings_from_path(tmp_path):
    cfg = tmp_path / "settings.yaml"
    cfg.write_text("server:\n  host: '0.0.0.0'\n  port: 9999\n")
    settings = load_settings(cfg)
    assert settings.server.host == "0.0.0.0"
    assert settings.server.port == 9999

def test_load_agent_hosts_empty(tmp_path):
    cfg = tmp_path / "agents.yaml"
    cfg.write_text("hosts: []\n")
    hosts = load_agent_hosts(cfg)
    assert hosts == []
```

- [ ] **Step 2: Run test, verify it fails**

Run: `pytest tests/test_config.py -v`
Expected: FAIL (module not found)

- [ ] **Step 3: Implement `src/config.py`**

Use pydantic `BaseModel` for `Settings`, with nested models: `ServerConfig`, `DatabaseConfig`, `TimeoutConfig`, `HealthCheckConfig`, `MergeConfig`. Load from YAML file using `pyyaml`. Default path: `config/settings.yaml` (relative to project root). `load_agent_hosts` returns list of dicts from `config/agents.yaml`.

Use `pathlib.Path(__file__).resolve().parents[1]` for ROOT detection (same pattern as old `workflow.py`).

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_config.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat: add config loader with pydantic settings models"
```

---

### Task 4: Database module (`src/database.py`)

**Files:**
- Create: `src/database.py`
- Create: `tests/test_database.py`

- [ ] **Step 1: Write tests**

```python
# tests/test_database.py
import pytest
from src.database import Database

@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()

async def test_connect_creates_tables(db):
    row = await db.fetchone("SELECT name FROM sqlite_master WHERE type='table' AND name='runs'")
    assert row is not None

async def test_insert_and_fetch_run(db):
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
        ("r1", "T-1", "/repo", "running", "INIT", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
    )
    run = await db.fetchone("SELECT * FROM runs WHERE id=?", ("r1",))
    assert run["ticket"] == "T-1"

async def test_transaction_rollback(db):
    try:
        async with db.transaction():
            await db.execute(
                "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) VALUES(?,?,?,?,?,?,?)",
                ("r2", "T-2", "/repo", "running", "INIT", "2026-01-01T00:00:00Z", "2026-01-01T00:00:00Z")
            )
            raise RuntimeError("test rollback")
    except RuntimeError:
        pass
    row = await db.fetchone("SELECT * FROM runs WHERE id=?", ("r2",))
    assert row is None
```

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_database.py -v`
Expected: FAIL

- [ ] **Step 3: Implement `src/database.py`**

`Database` class wrapping `aiosqlite`:
- `connect()`: open connection, execute schema, enable WAL
- `close()`: close connection
- `execute(sql, params)`: write operation
- `fetchone(sql, params)`: single row, returns dict
- `fetchall(sql, params)`: multiple rows, returns list of dicts
- `transaction()`: async context manager with commit/rollback
- All write operations go through a single connection (spec Section 13.9)

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_database.py -v`
Expected: PASS

- [ ] **Step 5: Commit**

```bash
git add src/database.py tests/test_database.py
git commit -m "feat: add async database module with aiosqlite"
```

---

### Task 5: Pydantic models and exceptions (`src/models.py`, `src/exceptions.py`)

**Files:**
- Create: `src/models.py`
- Create: `src/exceptions.py`

- [ ] **Step 1: Define custom exceptions (`src/exceptions.py`)**

```python
class NotFoundError(Exception):
    """Raised when a resource is not found (404)."""

class ConflictError(Exception):
    """Raised when operation conflicts with current state (409)."""
    def __init__(self, message: str, current_stage: str = None):
        super().__init__(message)
        self.current_stage = current_stage
```

- [ ] **Step 2: Define all request/response models**

Models needed (all from spec Section 3 and 12):

**Enums:**
- `RunStatus`: `running`, `completed`, `failed`, `cancelled`
- `Stage`: all 15 stage names from spec Section 5
- `GateName`: `req`, `design`, `dev`
- `ArtifactKind`: `req`, `design`, `adr`, `code`, `test-report`
- `ArtifactStatus`: `draft`, `submitted`, `approved`, `rejected`
- `JobStatus`: `starting`, `running`, `completed`, `failed`, `timeout`, `cancelled`, `interrupted`
- `RecoverAction`: `resume`, `redo`, `manual`

**Request models:**
- `CreateRunRequest`: ticket, repo_path, description?, preferences?
- `ApproveRequest`: gate, by, comment?
- `RejectRequest`: gate, by, reason
- `RetryRequest`: by, note?
- `RecoverRequest`: action (resume/redo/manual)
- `SubmitRequirementRequest`: content
- `CreateWebhookRequest`: url, events?, secret?
- `CreateAgentHostRequest`: id, host, agent_type, max_concurrent?, ssh_key?, labels?
- `UpdateAgentHostRequest`: host?, agent_type?, max_concurrent?, ssh_key?, labels?
- `MergeRequest`: priority?

**Response models:**
- `RunResponse`: run_id, ticket, status, current_stage, description, created_at, updated_at, warning?
- `RunDetailResponse`: extends RunResponse with steps, approvals, recent_events, artifacts
- `ArtifactResponse`: id, run_id, kind, path, version, status, byte_size, created_at
- `ArtifactContentResponse`: extends ArtifactResponse with content, diff_from_prev?
- `JobResponse`: id, run_id, host_id, agent_type, stage, status, started_at, ended_at
- `AgentHostResponse`: id, host, agent_type, max_concurrent, status, current_load
- `WebhookResponse`: id, url, events, status
- `ErrorResponse`: error, message, current_stage?, details?
- `HealthResponse`: status, uptime, db, active_runs, active_jobs

- [ ] **Step 3: Commit**

```bash
git add src/models.py src/exceptions.py
git commit -m "feat: add pydantic models and custom exceptions"
```

---

## Chunk 2: Core Logic (Git Utils, State Machine, Artifact Manager)

### Task 6: Git utilities (`src/git_utils.py`)

**Files:**
- Create: `src/git_utils.py`
- Create: `tests/test_git_utils.py`

- [ ] **Step 1: Write tests for git utility functions**

Test cases:
- `test_ensure_worktree_creates_new`: init a git repo, call `ensure_worktree`, verify worktree dir and branch exist
- `test_ensure_worktree_reuses_existing`: call twice, verify no error
- `test_get_diff_stat`: make some commits, verify diff stat output
- `test_get_commit_log`: make commits, verify log entries
- `test_check_conflicts_no_conflict`: two branches modifying different files → no conflicts
- `test_check_conflicts_with_conflict`: two branches modifying same file → conflict detected

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_git_utils.py -v`

- [ ] **Step 3: Implement `src/git_utils.py`**

All functions are `async` and use `asyncio.create_subprocess_exec` to run git:

```python
async def run_git(*args, cwd=None, check=True) -> tuple[str, str, int]:
    """Run a git command, return (stdout, stderr, returncode)."""

async def ensure_worktree(repo_path: str, ticket: str, phase: str, run_suffix: str = "") -> tuple[str, str]:
    """Create or reuse worktree. Returns (branch_name, worktree_path).
    Phase is 'design' or 'dev'.
    Branch naming: feat/{ticket}-{phase} (first run), feat/{ticket}-{phase}-{suffix} (subsequent)."""

async def get_diff_stat(worktree: str, base_commit: str) -> str:
    """Return `git diff --stat base_commit..HEAD`."""

async def get_commit_log(worktree: str, base_commit: str) -> list[dict]:
    """Return list of {hash, message, files_changed, insertions, deletions}."""

async def check_conflicts(worktree: str, target_branch: str = "main") -> list[str]:
    """Dry-run merge to detect conflicts. Returns list of conflicted file paths.
    Uses `git merge --no-commit --no-ff` then `git merge --abort`."""

async def rebase_on_main(worktree: str) -> bool:
    """Rebase current branch on main. Returns True if clean, False if conflicts."""

async def merge_to_main(repo_path: str, branch: str) -> tuple[bool, str]:
    """Merge branch into main. Returns (success, merge_commit_hash_or_error)."""

async def cleanup_worktree(repo_path: str, worktree: str, branch: str):
    """Remove worktree and delete local branch."""

async def stash_save(worktree: str, message: str) -> bool:
    """Git stash push. Returns True if something was stashed."""

async def stash_pop(worktree: str) -> bool:
    """Git stash pop. Returns True if successful."""

async def get_head_commit(worktree: str) -> str:
    """Return HEAD commit hash."""
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_git_utils.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/git_utils.py tests/test_git_utils.py
git commit -m "feat: add async git utility functions"
```

---

### Task 7: Artifact manager (`src/artifact_manager.py`)

**Files:**
- Create: `src/artifact_manager.py`
- Create: `tests/test_artifact_manager.py`

- [ ] **Step 1: Write tests**

Test cases:
- `test_register_artifact`: register a req artifact, verify it's in DB with correct fields
- `test_register_artifact_version_increment`: register, reject, register again → version=2
- `test_scan_design_artifacts`: create DES and ADR files in a temp dir, verify scanner finds them
- `test_scan_dev_artifacts`: create TEST-REPORT file, verify scanner finds it
- `test_get_artifact_content`: register artifact with real file, verify content retrieval
- `test_approve_artifact`: register, submit, approve → status changes correctly
- `test_reject_artifact`: register, submit, reject → review_comment recorded

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_artifact_manager.py -v`

- [ ] **Step 3: Implement `src/artifact_manager.py`**

```python
class ArtifactManager:
    def __init__(self, db: Database):
        self.db = db

    async def register(self, run_id, kind, path, stage, git_ref=None) -> int:
        """Register artifact. Computes content_hash and byte_size. Returns artifact id.
        Auto-increments version if same run_id+kind has a rejected version."""

    async def scan_and_register(self, run_id, ticket, stage, worktree, base_commit=None) -> list[dict]:
        """Scan worktree for artifacts based on stage (spec Section 13.7).
        Uses glob patterns: DES-{ticket}*.md, ADR-{ticket}*.md, TEST-REPORT-{ticket}*.md.
        For code artifacts, uses git log. Only registers new/changed files (content_hash check).
        Returns list of registered artifacts."""

    async def get_by_run(self, run_id, kind=None, status=None) -> list[dict]:
        """List artifacts for a run with optional filters."""

    async def get_content(self, artifact_id) -> str:
        """Read and return file content for an artifact."""

    async def get_diff(self, artifact_id) -> str | None:
        """Diff against previous version of same run_id+kind. Returns None if v1."""

    async def update_status(self, artifact_id, status, review_comment=None):
        """Update artifact status (submitted/approved/rejected)."""

    async def submit_all(self, run_id, stage):
        """Mark all draft artifacts for this run+stage as submitted."""

    def _compute_hash(self, filepath) -> str:
        """SHA256 of file content."""

    async def render_task(self, template_path, variables: dict, output_path) -> str:
        """Render a task template with {{variable}} substitution. Write to output_path. Return output_path."""
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_artifact_manager.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/artifact_manager.py tests/test_artifact_manager.py
git commit -m "feat: add artifact manager with scan, register, and versioning"
```

---

### Task 8: State machine (`src/state_machine.py`)

**Files:**
- Create: `src/state_machine.py`
- Create: `tests/test_state_machine.py`

- [ ] **Step 1: Write tests for state transitions**

Test the core state machine logic in isolation. Mock these dependencies (implemented in later tasks):
- `webhook_notifier`: mock `notify(event_type: str, payload: dict)` — async, returns None
- `agent_executor`: mock `dispatch(run_id, host, agent_type, task_file, worktree, timeout_sec)` — async, returns job_id str
- `host_manager`: mock `select_host(agent_type: str, preferred_host: str = None)` — async, returns dict or None
- `merge_manager`: mock as needed for merge stages

Key test cases:

```python
# Key test cases:
# test_init_to_req_collecting: new run auto-transitions on tick
# test_req_collecting_to_req_review: req file exists → transitions
# test_req_review_approve_to_design_queued: approval + tick → DESIGN_QUEUED
# test_req_review_reject_to_req_collecting: rejection → back to REQ_COLLECTING
# test_design_queued_no_host: no available host → stays in DESIGN_QUEUED
# test_design_queued_with_host: host available → DESIGN_DISPATCHED (mock executor)
# test_design_review_reject: → DESIGN_QUEUED with revision task
# test_failed_retry: FAILED → restores to failed_at_stage
# test_tick_idempotent: calling tick twice in same state doesn't duplicate side effects
# test_concurrent_approve_reject: second call raises ConflictError
```

- [ ] **Step 2: Run tests, verify fail**

Run: `pytest tests/test_state_machine.py -v`

- [ ] **Step 3: Implement `src/state_machine.py`**

```python
class StateMachine:
    def __init__(self, db, artifact_manager, host_manager, agent_executor, webhook_notifier):
        self.db = db
        self.artifacts = artifact_manager
        self.hosts = host_manager
        self.executor = agent_executor
        self.webhooks = webhook_notifier

    async def create_run(self, ticket, repo_path, description=None, preferences=None) -> dict:
        """Create new run, auto-tick to REQ_COLLECTING. Returns run dict.
        Check for duplicate ticket (spec 13.5) and return warning if found."""

    async def tick(self, run_id) -> dict:
        """Advance the run by one step based on current_stage. Idempotent.
        Returns updated run dict. Core dispatch table for all 15 stages."""

    async def approve(self, run_id, gate, by, comment=None) -> dict:
        """Approve a gate. Validates current_stage matches gate (spec 13.3).
        Records approval, ticks forward. Returns updated run."""

    async def reject(self, run_id, gate, by, reason) -> dict:
        """Reject a gate. Marks artifacts as rejected, rolls back stage.
        REQ_REVIEW → REQ_COLLECTING, DESIGN_REVIEW → DESIGN_QUEUED, DEV_REVIEW → DEV_QUEUED."""

    async def retry(self, run_id, by, note=None) -> dict:
        """Retry a FAILED run. Restores status=running, stage=failed_at_stage."""

    async def cancel(self, run_id, cleanup=False) -> dict:
        """Cancel a run (spec 13.4). Kill jobs, remove from merge queue, optionally cleanup worktrees."""

    async def submit_requirement(self, run_id, content) -> dict:
        """Write requirement file and tick (spec 13.1)."""

    # Internal methods:
    async def _tick_init(self, run): ...
    async def _tick_req_collecting(self, run): ...
    async def _tick_req_review(self, run): ...  # no-op, waits for approve/reject
    async def _tick_design_queued(self, run): ...
    async def _tick_design_dispatched(self, run): ...  # check job started
    async def _tick_design_running(self, run): ...  # check artifacts
    async def _tick_design_review(self, run): ...  # no-op, waits
    async def _tick_dev_queued(self, run): ...
    async def _tick_dev_dispatched(self, run): ...
    async def _tick_dev_running(self, run): ...
    async def _tick_dev_review(self, run): ...
    async def _tick_merge_queued(self, run): ...
    async def _tick_merging(self, run): ...
    async def _tick_merge_conflict(self, run): ...  # no-op, waits

    async def _update_stage(self, run_id, from_stage, to_stage, **event_payload):
        """Update run stage, record stage.changed event, snapshot, webhook."""

    async def _emit(self, run_id, event_type, payload=None):
        """Insert event and trigger webhook notification."""

    async def _snapshot(self, run_id):
        """Write state.json snapshot to .coop/runs/{run_id}/."""
```

Gate-to-stage mapping (spec 13.3):
```python
GATE_STAGES = {"req": "REQ_REVIEW", "design": "DESIGN_REVIEW", "dev": "DEV_REVIEW"}
```

- [ ] **Step 4: Run tests, verify pass**

Run: `pytest tests/test_state_machine.py -v`

- [ ] **Step 5: Commit**

```bash
git add src/state_machine.py tests/test_state_machine.py
git commit -m "feat: add state machine with 15-stage workflow and gate control"
```

---

## Chunk 3: Execution (Host Manager, Agent Executor, Job Manager)

### Task 9: Host manager (`src/host_manager.py`)

**Files:**
- Create: `src/host_manager.py`
- Create: `tests/test_host_manager.py`

- [ ] **Step 1: Write tests**

```python
# test_register_host: register a host, verify in DB
# test_select_host_least_loaded: 2 hosts, one has load=1, other load=0 → picks load=0
# test_select_host_filters_offline: offline host not selected
# test_select_host_filters_agent_type: codex host not selected for claude task
# test_select_host_respects_max_concurrent: at max → not selected
# test_select_host_preference: preferred host returned when available
# test_select_host_preference_fallback: preferred host offline → auto-select
# test_increment_decrement_load: load goes up on dispatch, down on complete
# test_load_from_config: load agents.yaml hosts into DB
```

- [ ] **Step 2: Run tests, verify fail**

- [ ] **Step 3: Implement `src/host_manager.py`**

```python
class HostManager:
    def __init__(self, db: Database):
        self.db = db

    async def register(self, id, host, agent_type, max_concurrent=1, ssh_key=None, labels=None):
        """Insert or update agent_hosts row."""

    async def remove(self, host_id):
        """Delete host from DB."""

    async def list_all(self) -> list[dict]:
        """Return all hosts with current load."""

    async def select_host(self, agent_type: str, preferred_host: str = None) -> dict | None:
        """Select best available host (spec Section 7 algorithm).
        1. Filter by agent_type match (or 'both')
        2. Filter status == 'online'
        3. Filter current_load < max_concurrent
        4. If preferred_host specified and available, use it
        5. Otherwise sort by current_load ASC, pick first
        Returns None if no host available."""

    async def increment_load(self, host_id):
        """current_load += 1"""

    async def decrement_load(self, host_id):
        """current_load -= 1 (floor at 0)"""

    async def set_status(self, host_id, status: str):
        """Update host status (online/offline)."""

    async def health_check(self, host_id) -> bool:
        """Check if host is reachable. Local: check claude/codex command exists.
        Remote: asyncssh connect with timeout. Update status accordingly."""

    async def load_from_config(self, hosts_config: list[dict]):
        """Bulk load hosts from agents.yaml config. Insert-or-update."""
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add src/host_manager.py tests/test_host_manager.py
git commit -m "feat: add host manager with auto-selection and health check"
```

---

### Task 10: Agent executor and job manager (`src/agent_executor.py`, `src/job_manager.py`)

**Files:**
- Create: `src/agent_executor.py`
- Create: `src/job_manager.py`
- Create: `tests/test_agent_executor.py`

- [ ] **Step 1: Write tests**

```python
# test_build_command_claude: verify correct claude -p command construction
# test_build_command_codex: verify correct codex -q command construction
# test_dispatch_local: dispatch to local host, mock subprocess, verify job created in DB
# test_dispatch_records_base_commit: verify base_commit is captured before dispatch
# test_on_complete_scans_artifacts: mock agent exit 0, verify artifacts scanned
# test_on_complete_ticks_state: verify state machine tick is called
# test_on_timeout_saves_snapshot: verify stash + snapshot on timeout
# test_on_timeout_sends_webhook: verify job.interrupted event
# test_cancel_job: running job killed, status set to cancelled
# test_recover_resume: stash pop, new task file, re-dispatch
# test_recover_redo: git reset hard, re-dispatch with original task
# test_output_streamed_to_file: stdout written to {coop}/jobs/{job_id}/stdout.log
```

- [ ] **Step 2: Run tests, verify fail**

- [ ] **Step 3: Implement `src/job_manager.py`**

```python
class JobManager:
    def __init__(self, db: Database):
        self.db = db

    async def create_job(self, run_id, host_id, agent_type, stage, task_file, worktree, base_commit, timeout_sec) -> str:
        """Insert job row, return job_id (job-{uuid})."""

    async def update_status(self, job_id, status, exit_code=None, output_log=None, snapshot_json=None):
        """Update job status and optional fields."""

    async def get_active_job(self, run_id) -> dict | None:
        """Get the most recent non-terminal job for a run."""

    async def get_jobs(self, run_id) -> list[dict]:
        """List all jobs for a run."""

    async def get_output(self, job_id) -> str:
        """Read stdout.log file for a job. Fallback to output_log field."""
```

- [ ] **Step 4: Implement `src/agent_executor.py`**

```python
import asyncio
import shlex

class AgentExecutor:
    def __init__(self, db, job_manager, host_manager, artifact_manager, state_machine_ref, webhook_notifier, config):
        # state_machine_ref is set after construction to avoid circular dep
        ...
        self._tasks: dict[str, asyncio.Task] = {}  # job_id → watch task

    async def dispatch(self, run_id, host, agent_type, task_file, worktree, timeout_sec) -> str:
        """Dispatch agent task. Returns job_id.
        1. Capture base_commit via git_utils.get_head_commit
        2. Create job record
        3. Build command
        4. Start process (local subprocess or asyncssh)
        5. Launch _watch as background asyncio.Task
        """

    def _build_command(self, agent_type: str, task_file: str) -> list[str]:
        """Build command parts.
        Claude: ["claude", "-p", task_content, "--output-format", "json", "--max-turns", "50"]
        Codex: ["codex", "-q", "--prompt", task_content]
        task_content is read from task_file."""

    async def _run_local(self, cmd_parts, worktree, job_id) -> asyncio.subprocess.Process:
        """Start local subprocess. Stdout/stderr piped to file."""

    async def _run_ssh(self, host, cmd_parts, worktree, job_id) -> asyncio.subprocess.Process:
        """Start remote SSH process via asyncssh (spec 13.8).
        Uses shlex.quote per argument."""

    async def _watch(self, job_id, process, run_id, timeout_sec):
        """Background task: wait for process, handle completion/timeout/error.
        On complete: scan artifacts, tick state machine.
        On timeout: kill, save snapshot, emit job.interrupted.
        On error (exit_code != 0): save output, emit job.failed."""

    async def _on_complete(self, job_id, run_id, stdout):
        """Agent finished successfully. Scan artifacts, tick."""

    async def _on_interrupted(self, job_id, run_id, reason):
        """Save git stash, record snapshot (spec Section 9), emit event."""

    async def cancel(self, job_id):
        """Kill running process, cancel watch task, update job status."""

    async def recover(self, run_id, action: str):
        """Resume/redo/manual recovery (spec Section 9).
        resume: stash pop, generate resume task, re-dispatch.
        redo: git reset --hard base_commit, re-dispatch with original task.
        manual: just update job status, no re-dispatch."""

    async def restore_on_startup(self):
        """On API server start: check jobs table for status=running/starting.
        If process no longer alive → mark as interrupted."""
```

- [ ] **Step 5: Run tests, verify pass**

Run: `pytest tests/test_agent_executor.py -v`

- [ ] **Step 6: Commit**

```bash
git add src/agent_executor.py src/job_manager.py tests/test_agent_executor.py
git commit -m "feat: add agent executor with local/SSH dispatch and job lifecycle"
```

---

## Chunk 4: Integration (Webhook Notifier, Merge Manager)

### Task 11: Webhook notifier (`src/webhook_notifier.py`)

**Files:**
- Create: `src/webhook_notifier.py`
- Create: `tests/test_webhook_notifier.py`

- [ ] **Step 1: Write tests**

```python
# test_register_webhook: insert webhook, verify in DB
# test_notify_sends_to_all_active: register 2 webhooks, emit event, both receive POST
# test_notify_filters_by_event_type: webhook watches only "gate.waiting", other events skipped
# test_notify_skips_paused: paused webhook not called
# test_gate_waiting_includes_artifacts: verify artifact content is included in payload
# test_retry_on_failure: mock HTTP 500, verify retried 3 times
# test_retry_exhausted_logs_event: after 3 retries, webhook.delivery_failed event recorded
```

- [ ] **Step 2: Run tests, verify fail**

- [ ] **Step 3: Implement `src/webhook_notifier.py`**

```python
import httpx

class WebhookNotifier:
    def __init__(self, db: Database):
        self.db = db
        self._client = httpx.AsyncClient(timeout=10)

    async def register(self, url, events=None, secret=None) -> str:
        """Register webhook, return webhook id."""

    async def remove(self, webhook_id):
        """Delete webhook."""

    async def list_all(self) -> list[dict]:
        """Return all webhooks."""

    async def notify(self, event_type: str, payload: dict):
        """Send event to all matching active webhooks.
        For gate.waiting events: auto-fetch and attach artifact content (spec Section 3).
        Retry policy: 3 attempts with delays 5s, 30s, 300s (spec 13.11).
        On exhausted retries: record webhook.delivery_failed event."""

    async def _deliver(self, webhook, event_type, payload) -> bool:
        """POST to webhook URL. Returns True on success (2xx)."""

    async def close(self):
        """Close httpx client."""
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add src/webhook_notifier.py tests/test_webhook_notifier.py
git commit -m "feat: add webhook notifier with retry and artifact enrichment"
```

---

### Task 12: Merge manager (`src/merge_manager.py`)

**Files:**
- Create: `src/merge_manager.py`
- Create: `tests/test_merge_manager.py`

- [ ] **Step 1: Write tests**

```python
# test_enqueue: add to merge queue, verify queued status
# test_process_next_clean_merge: one item in queue, no conflict → merged
# test_process_next_conflict: rebase conflict → status=conflict, webhook sent
# test_queue_order_fifo: two items, first is processed first
# test_queue_order_priority: higher priority processed first
# test_skip_item: skip a conflicted item, next one proceeds
# test_only_one_merging_at_a_time: while one is merging, another stays queued
# test_cleanup_after_merge: worktree and branch cleaned up after successful merge
```

- [ ] **Step 2: Run tests, verify fail**

- [ ] **Step 3: Implement `src/merge_manager.py`**

```python
class MergeManager:
    def __init__(self, db, webhook_notifier):
        self.db = db
        self.webhooks = webhook_notifier

    async def enqueue(self, run_id, repo_path, branch, priority=0) -> int:
        """Add to merge_queue. Returns queue entry id."""

    async def process_next(self, repo_path) -> dict | None:
        """Process the next queued item for a repo.
        1. Find first item with status=queued, ordered by priority DESC, queued_at ASC
        2. Set status=merging
        3. Rebase on main (git_utils.rebase_on_main)
        4. If conflict → status=conflict, emit merge.conflict
        5. If clean → merge to main (git_utils.merge_to_main)
        6. On success → status=merged, emit merge.completed, cleanup worktree
        Returns the processed queue entry or None if queue empty."""

    async def skip(self, run_id):
        """Mark queue entry as skipped."""

    async def get_queue(self, repo_path) -> list[dict]:
        """List queue for a repo."""

    async def check_conflicts(self, run_id, repo_path, worktree) -> list[dict]:
        """Run conflict detection against main and other active branches.
        Returns list of {file, with_branch, type}."""
```

- [ ] **Step 4: Run tests, verify pass**

- [ ] **Step 5: Commit**

```bash
git add src/merge_manager.py tests/test_merge_manager.py
git commit -m "feat: add merge manager with queue, conflict detection, and cleanup"
```

---

## Chunk 5: API Routes and App Entry Point

### Task 13: App entry point and dependency wiring (`src/app.py`)

**Files:**
- Create: `src/app.py`

- [ ] **Step 1: Implement `src/app.py`**

```python
from contextlib import asynccontextmanager
from fastapi import FastAPI
from src.config import load_settings, load_agent_hosts
from src.database import Database
from src.artifact_manager import ArtifactManager
from src.host_manager import HostManager
from src.job_manager import JobManager
from src.agent_executor import AgentExecutor
from src.webhook_notifier import WebhookNotifier
from src.merge_manager import MergeManager
from src.state_machine import StateMachine

@asynccontextmanager
async def lifespan(app: FastAPI):
    # Startup
    settings = load_settings()
    db = Database(db_path=settings.database.path, schema_path="db/schema.sql")
    await db.connect()

    artifacts = ArtifactManager(db)
    hosts = HostManager(db)
    jobs = JobManager(db)
    webhooks = WebhookNotifier(db)
    merger = MergeManager(db, webhooks)

    executor = AgentExecutor(db, jobs, hosts, artifacts, None, webhooks, settings)
    sm = StateMachine(db, artifacts, hosts, executor, webhooks)
    executor.state_machine_ref = sm  # break circular dep

    # Load hosts from config
    agent_config = load_agent_hosts()
    await hosts.load_from_config(agent_config)

    # Restore interrupted jobs
    await executor.restore_on_startup()

    # Store refs on app.state for route access
    app.state.db = db
    app.state.sm = sm
    app.state.artifacts = artifacts
    app.state.hosts = hosts
    app.state.jobs = jobs
    app.state.executor = executor
    app.state.webhooks = webhooks
    app.state.merger = merger
    app.state.settings = settings
    app.state.start_time = time.time()

    yield

    # Shutdown
    await webhooks.close()
    await db.close()

app = FastAPI(title="cooagents", version="0.2.0", lifespan=lifespan)

# Register routes
from routes.runs import router as runs_router
from routes.artifacts import router as artifacts_router
from routes.agent_hosts import router as hosts_router
from routes.webhooks import router as webhooks_router
from routes.repos import router as repos_router

app.include_router(runs_router, prefix="/api/v1")
app.include_router(artifacts_router, prefix="/api/v1")
app.include_router(hosts_router, prefix="/api/v1")
app.include_router(webhooks_router, prefix="/api/v1")
app.include_router(repos_router, prefix="/api/v1")

@app.get("/health")
async def health(request: Request):
    db = request.app.state.db
    active_runs = await db.fetchone("SELECT COUNT(*) as c FROM runs WHERE status='running'")
    active_jobs = await db.fetchone("SELECT COUNT(*) as c FROM jobs WHERE status IN ('starting','running')")
    return {
        "status": "ok",
        "uptime": int(time.time() - request.app.state.start_time),
        "db": "connected",
        "active_runs": active_runs["c"],
        "active_jobs": active_jobs["c"],
    }
```

- [ ] **Step 2: Verify app starts**

Run: `uvicorn src.app:app --host 127.0.0.1 --port 8321 --workers 1`
Expected: Server starts, `/health` returns 200.

- [ ] **Step 3: Commit**

```bash
git add src/app.py
git commit -m "feat: add FastAPI app with lifespan wiring and health endpoint"
```

---

### Task 13b: Background scheduler (`src/scheduler.py`)

**Files:**
- Create: `src/scheduler.py`
- Create: `tests/test_scheduler.py`

- [ ] **Step 1: Write tests**

```python
# test_health_check_marks_offline: mock SSH failure, verify host marked offline
# test_health_check_restores_online: mock SSH success after offline, verify restored
# test_dispatch_timeout_marks_failed: job in DISPATCHED >5min → FAILED
# test_running_timeout_interrupts: job in RUNNING past timeout → interrupted
# test_review_reminder: run in REVIEW >24h → webhook reminder sent
# test_queued_notification: run in QUEUED >10min → webhook notification sent
# test_stale_worktree_cleanup: FAILED run >7 days → notification sent
```

- [ ] **Step 2: Run tests, verify fail**

- [ ] **Step 3: Implement `src/scheduler.py`**

```python
import asyncio

class Scheduler:
    def __init__(self, db, host_manager, job_manager, agent_executor, webhook_notifier, config):
        self._tasks: list[asyncio.Task] = []

    async def start(self):
        """Start all periodic background tasks. Called during app lifespan startup."""
        self._tasks.append(asyncio.create_task(self._health_check_loop()))
        self._tasks.append(asyncio.create_task(self._timeout_enforcement_loop()))
        self._tasks.append(asyncio.create_task(self._reminder_loop()))

    async def stop(self):
        """Cancel all background tasks. Called during app lifespan shutdown."""
        for task in self._tasks:
            task.cancel()
        await asyncio.gather(*self._tasks, return_exceptions=True)

    async def _health_check_loop(self):
        """Every {config.health_check.interval} seconds:
        - For each host, call host_manager.health_check(host_id)
        - If status changed → emit host.online or host.offline event
        - If host comes online and there are QUEUED runs → tick them"""
        while True:
            await asyncio.sleep(self.config.health_check.interval)
            hosts = await self.host_manager.list_all()
            for host in hosts:
                was_online = host["status"] == "online"
                is_online = await self.host_manager.health_check(host["id"])
                if was_online and not is_online:
                    await self.webhooks.notify("host.offline", {"host_id": host["id"]})
                elif not was_online and is_online:
                    await self.webhooks.notify("host.online", {"host_id": host["id"]})

    async def _timeout_enforcement_loop(self):
        """Every 30 seconds:
        - Find jobs with status='starting' older than dispatch_startup timeout → mark FAILED
        - Find jobs with status='running' older than their timeout_sec → trigger interrupt
        - Find runs in QUEUED states >10min since last notification → re-notify"""
        while True:
            await asyncio.sleep(30)
            # Check DISPATCHED timeouts
            # Check RUNNING timeouts
            # Check QUEUED notifications

    async def _reminder_loop(self):
        """Every hour:
        - Find runs in REVIEW stages >24h since last gate.waiting event → re-send webhook
        - Find FAILED runs with worktrees >7 days old → notify for cleanup"""
        while True:
            await asyncio.sleep(3600)
            # Check review reminders
            # Check stale worktree cleanup
```

- [ ] **Step 4: Wire into `src/app.py` lifespan**

Add to lifespan startup after all managers are created:
```python
scheduler = Scheduler(db, hosts, jobs, executor, webhooks, settings)
await scheduler.start()
app.state.scheduler = scheduler
# In shutdown:
await scheduler.stop()
```

- [ ] **Step 5: Run tests, verify pass**

- [ ] **Step 6: Commit**

```bash
git add src/scheduler.py tests/test_scheduler.py src/app.py
git commit -m "feat: add background scheduler for health checks, timeouts, and reminders"
```

---

### Task 14: Runs routes (`routes/runs.py`)

**Files:**
- Create: `routes/runs.py`
- Create: `tests/test_api.py`

- [ ] **Step 1: Implement `routes/runs.py`**

All endpoints from spec Section 3 — task lifecycle:

```python
from fastapi import APIRouter, Request, HTTPException
from src.models import *

router = APIRouter(tags=["runs"])

@router.post("/runs", status_code=201, response_model=RunResponse)
async def create_run(req: CreateRunRequest, request: Request):
    sm = request.app.state.sm
    result = await sm.create_run(req.ticket, req.repo_path, req.description, req.preferences)
    return result

@router.get("/runs", response_model=list[RunResponse])
async def list_runs(request: Request, status: str = None, limit: int = 20):
    ...

@router.get("/runs/{run_id}", response_model=RunDetailResponse)
async def get_run(run_id: str, request: Request):
    ...

@router.post("/runs/{run_id}/tick", response_model=RunResponse)
async def tick_run(run_id: str, request: Request):
    ...

@router.post("/runs/{run_id}/approve", response_model=RunResponse)
async def approve_run(run_id: str, req: ApproveRequest, request: Request):
    ...

@router.post("/runs/{run_id}/reject", response_model=RunResponse)
async def reject_run(run_id: str, req: RejectRequest, request: Request):
    ...

@router.post("/runs/{run_id}/retry", response_model=RunResponse)
async def retry_run(run_id: str, req: RetryRequest, request: Request):
    ...

@router.post("/runs/{run_id}/recover", response_model=RunResponse)
async def recover_run(run_id: str, req: RecoverRequest, request: Request):
    ...

@router.post("/runs/{run_id}/submit-requirement", response_model=RunResponse)
async def submit_requirement(run_id: str, req: SubmitRequirementRequest, request: Request):
    ...

@router.delete("/runs/{run_id}", response_model=RunResponse)
async def cancel_run(run_id: str, request: Request, cleanup: bool = False):
    ...
```

Use FastAPI exception handlers for unified error responses (spec 13.2):

```python
from fastapi.responses import JSONResponse

@app.exception_handler(NotFoundError)
async def not_found_handler(request, exc):
    return JSONResponse(status_code=404, content={"error": "not_found", "message": str(exc)})

@app.exception_handler(ConflictError)
async def conflict_handler(request, exc):
    return JSONResponse(status_code=409, content={"error": "conflict", "message": str(exc)})
```

- [ ] **Step 2: Write API integration tests**

```python
# tests/test_api.py
import pytest
from httpx import AsyncClient, ASGITransport
from src.app import app

@pytest.fixture
async def client():
    async with AsyncClient(transport=ASGITransport(app=app), base_url="http://test") as c:
        yield c

async def test_create_run(client):
    resp = await client.post("/api/v1/runs", json={"ticket": "T-1", "repo_path": "/tmp/repo"})
    assert resp.status_code == 201
    data = resp.json()
    assert data["status"] == "running"
    assert data["current_stage"] == "REQ_COLLECTING"

async def test_get_run(client):
    # create then get
    resp = await client.post("/api/v1/runs", json={"ticket": "T-2", "repo_path": "/tmp/repo"})
    run_id = resp.json()["run_id"]
    resp = await client.get(f"/api/v1/runs/{run_id}")
    assert resp.status_code == 200

async def test_get_run_not_found(client):
    resp = await client.get("/api/v1/runs/nonexistent")
    assert resp.status_code == 404

async def test_approve_wrong_stage(client):
    resp = await client.post("/api/v1/runs", json={"ticket": "T-3", "repo_path": "/tmp/repo"})
    run_id = resp.json()["run_id"]
    resp = await client.post(f"/api/v1/runs/{run_id}/approve", json={"gate": "req", "by": "user"})
    assert resp.status_code == 409  # not in REQ_REVIEW stage

async def test_health(client):
    resp = await client.get("/health")
    assert resp.status_code == 200
    assert resp.json()["status"] == "ok"
```

- [ ] **Step 3: Run tests, verify pass**

Run: `pytest tests/test_api.py -v`

- [ ] **Step 4: Commit**

```bash
git add routes/runs.py tests/test_api.py
git commit -m "feat: add runs API routes with error handling"
```

---

### Task 15: Artifacts routes (`routes/artifacts.py`)

**Files:**
- Create: `routes/artifacts.py`

- [ ] **Step 1: Implement `routes/artifacts.py`**

```python
router = APIRouter(tags=["artifacts"])

@router.get("/runs/{run_id}/artifacts", response_model=list[ArtifactResponse])
async def list_artifacts(run_id: str, request: Request, kind: str = None, status: str = None):
    ...

@router.get("/runs/{run_id}/artifacts/{artifact_id}", response_model=ArtifactResponse)
async def get_artifact(run_id: str, artifact_id: int, request: Request):
    ...

@router.get("/runs/{run_id}/artifacts/{artifact_id}/content", response_model=ArtifactContentResponse)
async def get_artifact_content(run_id: str, artifact_id: int, request: Request):
    ...

@router.get("/runs/{run_id}/artifacts/{artifact_id}/diff")
async def get_artifact_diff(run_id: str, artifact_id: int, request: Request):
    ...
```

- [ ] **Step 2: Commit**

```bash
git add routes/artifacts.py
git commit -m "feat: add artifacts API routes"
```

---

### Task 16: Agent hosts routes (`routes/agent_hosts.py`)

**Files:**
- Create: `routes/agent_hosts.py`

- [ ] **Step 1: Implement all endpoints from spec Section 3**

```python
router = APIRouter(tags=["agent-hosts"])

@router.get("/agent-hosts", response_model=list[AgentHostResponse])
@router.post("/agent-hosts", status_code=201, response_model=AgentHostResponse)
@router.put("/agent-hosts/{host_id}", response_model=AgentHostResponse)
@router.delete("/agent-hosts/{host_id}")
@router.post("/agent-hosts/{host_id}/check")
```

- [ ] **Step 2: Commit**

```bash
git add routes/agent_hosts.py
git commit -m "feat: add agent-hosts API routes"
```

---

### Task 17: Webhooks and repos routes (`routes/webhooks.py`, `routes/repos.py`)

**Files:**
- Create: `routes/webhooks.py`
- Create: `routes/repos.py`

- [ ] **Step 1: Implement webhook routes**

```python
# routes/webhooks.py
@router.post("/webhooks", status_code=201)
@router.delete("/webhooks/{webhook_id}")
@router.get("/webhooks/{webhook_id}/deliveries")  # spec 13.11
```

- [ ] **Step 2: Implement repo and job routes**

```python
# routes/repos.py
@router.get("/runs/{run_id}/jobs")
@router.get("/runs/{run_id}/jobs/{job_id}/output")
@router.get("/runs/{run_id}/conflicts")
@router.post("/runs/{run_id}/merge")
@router.post("/runs/{run_id}/merge-skip")  # spec 13.12

# Repo-scoped endpoints — use query param since repo_path contains slashes:
@router.get("/repos")  # ?path=/path/to/repo → list runs for that repo
@router.get("/repos/merge-queue")  # ?path=/path/to/repo → merge queue for repo
```

- [ ] **Step 3: Commit**

```bash
git add routes/webhooks.py routes/repos.py
git commit -m "feat: add webhook and repo API routes"
```

---

## Chunk 6: Templates, Config Files, Bootstrap, and OpenClaw Integration

### Task 18: Update task templates

**Files:**
- Modify: `templates/TASK-claude.md`
- Modify: `templates/TASK-codex.md`
- Create: `templates/TASK-claude-revision.md`
- Create: `templates/TASK-codex-revision.md`
- Create: `templates/TASK-resume.md`

- [ ] **Step 1: Update `TASK-claude.md`**

Keep existing content but update paths to use worktree-relative paths. Remove ACK instructions (no longer needed — non-interactive mode returns output directly). Add output format expectations.

- [ ] **Step 2: Update `TASK-codex.md`**

Same updates as claude template.

- [ ] **Step 3: Create `TASK-claude-revision.md`**

Template for design revision after rejection. Includes:
- `{{revision_version}}` — version number
- `{{reject_reason}}` — reviewer's feedback
- `{{original_design_path}}` — path to previous design doc
- All fields from original template

- [ ] **Step 4: Create `TASK-codex-revision.md`**

Same pattern for dev revision.

- [ ] **Step 5: Create `TASK-resume.md`**

Template for resuming interrupted work (spec Section 9):
- `{{resume_count}}` — how many times resumed
- `{{interrupt_reason}}` — why interrupted
- `{{commits_made}}` — git log of completed work
- `{{diff_stat}}` — files changed so far
- `{{agent_output_tail}}` — last output before interruption
- `{{original_task_content}}` — full original task

- [ ] **Step 6: Create `templates/WEBHOOK-messages.yaml`**

Define message templates for each webhook event type. Used by webhook_notifier for formatting outgoing payloads. Structure:

```yaml
stage.changed:
  message: "任务 {{ticket}} 阶段变更: {{from}} → {{to}}"
gate.waiting:
  message: "任务 {{ticket}} 等待审阅 ({{gate}})"
gate.approved:
  message: "任务 {{ticket}} 审批通过 ({{gate}})"
gate.rejected:
  message: "任务 {{ticket}} 已驳回 ({{gate}}): {{reason}}"
job.interrupted:
  message: "任务 {{ticket}} 执行中断: {{reason}}"
run.completed:
  message: "任务 {{ticket}} 已完成"
run.failed:
  message: "任务 {{ticket}} 失败: {{error}}"
# ... all event types from spec Section 12
```

- [ ] **Step 7: Commit**

```bash
git add templates/
git commit -m "feat: update task templates and add revision/resume/webhook templates"
```

---

### Task 19: OpenClaw tools definition (`docs/openclaw-tools.json`)

**Files:**
- Create: `docs/openclaw-tools.json`

- [ ] **Step 1: Create function calling definition file**

JSON file defining all API operations as tools for OpenClaw's function calling interface. Each tool has: name, description (Chinese), parameters with types and descriptions, and example usage.

Tools to define:
- `create_task` — POST /runs
- `list_tasks` — GET /runs
- `get_task_status` — GET /runs/{run_id}
- `approve_gate` — POST /runs/{run_id}/approve
- `reject_gate` — POST /runs/{run_id}/reject
- `submit_requirement` — POST /runs/{run_id}/submit-requirement
- `retry_task` — POST /runs/{run_id}/retry
- `recover_task` — POST /runs/{run_id}/recover
- `cancel_task` — DELETE /runs/{run_id}
- `get_artifact` — GET /runs/{run_id}/artifacts/{id}/content
- `list_artifacts` — GET /runs/{run_id}/artifacts

- [ ] **Step 2: Commit**

```bash
git add docs/openclaw-tools.json
git commit -m "feat: add OpenClaw function calling tools definition"
```

---

### Task 20: Bootstrap script and documentation

**Files:**
- Modify: `scripts/bootstrap.sh`
- Modify: `README.md`

- [ ] **Step 1: Update `scripts/bootstrap.sh`**

New bootstrap flow:
1. Check dependencies: python3 (>=3.11), pip, git
2. Install Python dependencies: `pip install -r requirements.txt`
3. Create runtime dirs: `.coop/runs`, `.coop/jobs`
4. Initialize DB with new schema
5. Backup old DB if exists (`.coop/state.db` → `.coop/state.db.bak`)
6. Print quick-start: `uvicorn src.app:app --host 127.0.0.1 --port 8321`

- [ ] **Step 2: Update `README.md`**

Update with:
- New architecture overview
- Quick start (bootstrap → configure agents.yaml → start server)
- API documentation pointer (auto-generated at `/docs`)
- OpenClaw integration guide
- Link to spec document

- [ ] **Step 3: Commit**

```bash
git add scripts/bootstrap.sh README.md
git commit -m "feat: update bootstrap script and README for API architecture"
```

---

### Task 21: End-to-end integration test

**Files:**
- Create: `tests/test_e2e.py`

- [ ] **Step 1: Write end-to-end workflow test**

Test the full happy path without real agent execution (mock agent_executor.dispatch):

```python
async def test_full_workflow_happy_path(client, tmp_path):
    """
    1. POST /runs → REQ_COLLECTING
    2. POST /runs/{id}/submit-requirement → REQ_REVIEW
    3. GET /runs/{id}/artifacts → req artifact listed
    4. POST /runs/{id}/approve {gate: req} → DESIGN_QUEUED
    5. Mock agent completes → DESIGN_REVIEW
    6. GET /runs/{id}/artifacts?kind=design → design artifact
    7. POST /runs/{id}/approve {gate: design} → DEV_QUEUED
    8. Mock agent completes → DEV_REVIEW
    9. POST /runs/{id}/approve {gate: dev} → MERGE_QUEUED
    10. Mock merge succeeds → MERGED
    """
```

- [ ] **Step 2: Write rejection and retry test**

```python
async def test_design_rejection_and_redo(client, tmp_path):
    """
    1. Get to DESIGN_REVIEW
    2. POST /runs/{id}/reject → DESIGN_QUEUED
    3. Mock agent re-runs → DESIGN_REVIEW with version=2
    4. POST /runs/{id}/approve → DEV_QUEUED
    """
```

- [ ] **Step 3: Write cancellation test**

```python
async def test_cancel_running_task(client, tmp_path):
    """
    1. Create run, get to DEV_RUNNING with mock agent
    2. DELETE /runs/{id}?cleanup=true
    3. Verify status=cancelled, job killed, worktree cleaned
    """
```

- [ ] **Step 4: Write recovery test**

```python
async def test_recover_interrupted_task(client, tmp_path):
    """
    1. Create run, get to DEV_RUNNING
    2. Mock agent timeout → job.interrupted
    3. POST /runs/{id}/recover {action: resume}
    4. Verify new job dispatched with resume task file
    """
```

- [ ] **Step 5: Run all tests**

Run: `pytest tests/ -v`
Expected: All tests pass.

- [ ] **Step 6: Commit**

```bash
git add tests/test_e2e.py
git commit -m "test: add end-to-end workflow integration tests"
```

---

### Task 22: Final verification and cleanup

- [ ] **Step 1: Run full test suite**

Run: `pytest tests/ -v --tb=short`
Expected: All tests pass.

- [ ] **Step 2: Start server and test health endpoint**

Run: `uvicorn src.app:app --host 127.0.0.1 --port 8321 --workers 1`
Then: `curl http://127.0.0.1:8321/health`
Expected: `{"status": "ok", ...}`

- [ ] **Step 3: Verify OpenAPI docs generated**

Open: `http://127.0.0.1:8321/docs`
Expected: All endpoints listed with correct request/response schemas.

- [ ] **Step 4: Final commit**

```bash
git add -A
git commit -m "chore: final cleanup and verification"
```
