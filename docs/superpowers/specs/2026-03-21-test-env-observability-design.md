# Test Environment Observability Design

**Date:** 2026-03-21
**Status:** Approved
**Scope:** cooagents test environment — enhanced error logging, chain tracing, and diagnostic APIs for openclaw integration debugging

## Problem

In the openclaw/cooagents integration test environment, cooagents provides insufficient error detail and chain traceability. When exceptions occur, it is difficult to determine which run/job failed at which stage, for what reason, and what the full state transition path was. This makes debugging complex and chain reconstruction unclear.

### Current Gaps

| Dimension | Current State | Problem |
|-----------|--------------|---------|
| Logging | Only 3 files have `logging` statements, no centralized config | Most modules have no logging; logs are unstructured printf-style |
| Exception handling | Custom exception hierarchy exists, but 6+ `except: pass` in acpx_executor | Silent exception swallowing hides failure causes |
| Chain tracing | No correlation ID, no trace/span mechanism | Requests cannot be traced across modules; run_id/job_id not threaded through logs |
| Event audit | `events` table records events | Event context is incomplete, lacks error detail |
| Webhook delivery | Has retry and idempotency | Delivery failure recording is insufficient, no success rate stats |
| Health check | `/health` endpoint + background polling | No request latency, error rate, or throughput metrics |

## Design Overview

**Approach:** Correlation Context + Structured Event Stream + Diagnostic APIs

- Lightweight, embedded solution — no external services, all within cooagents process
- Extend existing `events` table with tracing fields
- New diagnostic APIs for openclaw to actively query
- Fire-and-forget event emission — tracing never affects business logic

## Architecture

### Three-Layer Tracing

All layers are linked by a unified `trace_id`:

1. **Request Layer** — FastAPI middleware intercepts each request, generates `trace_id`, records params/response/duration
2. **Run Layer** — State machine stage transitions, gate approval decisions produce events linked to `run_id` + `trace_id`
3. **Job Layer** — AcpxExecutor session lifecycle, output, exceptions produce events linked to `job_id` + `run_id` + `trace_id`

All events write to the extended `events` table. OpenClaw queries via 3 diagnostic APIs.

## Correlation Context Propagation

### New Module: `src/trace_context.py` (under `src/` alongside existing modules)

Based on Python `contextvars` (async-safe, zero-dependency):

```python
_trace_id:  ContextVar[str]        # request-level unique ID
_run_id:    ContextVar[str | None]  # current run
_job_id:    ContextVar[str | None]  # current job
_span_type: ContextVar[str]         # "request" | "run" | "job"

def new_trace(trace_id: str = None) -> Token   # generate and set new trace_id
def bind_run(run_id: str) -> Token             # bind run context
def bind_job(job_id: str) -> Token             # bind job context
def get_context() -> dict                       # return current {trace_id, run_id, job_id, span_type}
```

### Propagation Path

1. **API request enters** → `TraceMiddleware.dispatch()`:
   - Read `X-Trace-Id` header or auto-generate `uuid4().hex[:16]`
   - `new_trace(trace_id)` → write to contextvars
   - Response header writes back `X-Trace-Id`
   - Emit `request.received` / `request.completed`

2. **contextvars automatically propagate through async/await** — no manual passing needed

3. **Route handler → StateMachine**:
   - `bind_run(run_id)` → append run context
   - Stage transitions emit `stage.transition` etc.
   - `get_context()` automatically includes `{trace_id, run_id}`

4. **StateMachine → AcpxExecutor**:
   - `bind_job(job_id)` → append job context
   - Session lifecycle emits `session.*` events
   - `get_context()` automatically includes `{trace_id, run_id, job_id}`

5. **Background tasks (Scheduler)**:
   - No API request context → auto-generate internal trace_id with `sched-` prefix
   - `bind_run(run_id)` then normal tracing applies

### OpenClaw Integration

- OpenClaw can optionally send `X-Trace-Id` header; cooagents uses it as-is
- Response header writes back `X-Trace-Id` for subsequent queries
- Works without header (auto-generated)

## Business Flow Protection

All tracing code follows strict non-interference principles:

1. **Fire-and-forget emission** — trace events go through an async queue, never block business calls. Write failures do not affect business flow.
2. **Independent exception boundary** — any exception in tracing code (contextvars read failure, event write failure) is caught internally, never propagated upward.
3. **Lock-free design** — `contextvars` is per-task isolated, no cross-coroutine contention. Event writes use an async queue, not sharing database transactions with business logic.
4. **Zero-intrusion middleware** — records once at request entry and exit, does not modify request/response content.
5. **Degradable** — if `trace_context` module fails to load, all `get_context()` calls return empty dict; event writes degrade to existing behavior without context.

### Event Emission Interface

```python
# Bounded queue — drops silently when full to maintain fire-and-forget guarantee
_event_queue: asyncio.Queue = asyncio.Queue(maxsize=2048)

async def emit_trace_event(event_type: str, payload: dict = None,
                           level: str = "info", error_detail: str = None):
    """Fire-and-forget — never raises, never blocks business logic."""
    try:
        ctx = get_context()  # {trace_id, run_id, job_id}
        _event_queue.put_nowait((event_type, ctx, payload, level, error_detail))
    except (asyncio.QueueFull, Exception):
        pass  # tracing failure must not affect business
```

### Async Queue Consumer

A background `asyncio.Task` started in `app.py` lifespan consumes from `_event_queue` and writes to the database:

```python
async def _trace_consumer(db: Database):
    """Background task that drains the event queue and batch-writes to DB."""
    while True:
        batch = []
        # Block on first item
        item = await _event_queue.get()
        batch.append(item)
        # Drain any additional queued items (up to 64 per batch)
        while len(batch) < 64:
            try:
                batch.append(_event_queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        # Batch INSERT into events table
        try:
            await db.executemany(
                "INSERT INTO events (run_id, event_type, payload_json, created_at, "
                "trace_id, job_id, span_type, level, duration_ms, error_detail, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                [_item_to_row(item) for item in batch]
            )
        except Exception:
            pass  # consumer must never crash — events are best-effort
```

**Lifecycle:**
- Started as `asyncio.create_task(_trace_consumer(db))` in `app.py` lifespan `startup`
- On shutdown: cancel the task, drain remaining items with a 3-second timeout, then close
- Queue `maxsize=2048`: if producers outpace the consumer, `put_nowait` raises `QueueFull` which is silently caught — events are dropped rather than blocking business logic

## Events Table Extension

### Schema Migration

Migration is performed in `database.py` `_apply_compat_migrations()`, using the existing pattern of checking `PRAGMA table_info` before adding columns.

**Step 1: Add new columns (backward-compatible ALTER TABLE):**

```sql
-- New columns (all nullable or with defaults — existing data unaffected)
ALTER TABLE events ADD COLUMN trace_id     TEXT;
ALTER TABLE events ADD COLUMN job_id       TEXT;
ALTER TABLE events ADD COLUMN span_type    TEXT DEFAULT 'system';
ALTER TABLE events ADD COLUMN level        TEXT DEFAULT 'info';
ALTER TABLE events ADD COLUMN duration_ms  INTEGER;
ALTER TABLE events ADD COLUMN error_detail TEXT;
ALTER TABLE events ADD COLUMN source       TEXT;
```

**Step 2: Rebuild events table to make run_id nullable:**

SQLite does not support `ALTER COLUMN`. The migration creates a new table, copies data, and swaps:

```sql
-- Rebuild events table with run_id nullable (removes NOT NULL + FK constraint)
CREATE TABLE IF NOT EXISTS events_new (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT REFERENCES runs(id),    -- now nullable
  event_type   TEXT NOT NULL,
  payload_json TEXT,
  created_at   TEXT NOT NULL,
  trace_id     TEXT,
  job_id       TEXT,
  span_type    TEXT DEFAULT 'system',
  level        TEXT DEFAULT 'info',
  duration_ms  INTEGER,
  error_detail TEXT,
  source       TEXT
);
INSERT INTO events_new SELECT id, run_id, event_type, payload_json, created_at,
  trace_id, job_id, span_type, level, duration_ms, error_detail, source FROM events;
DROP TABLE events;
ALTER TABLE events_new RENAME TO events;
```

Note: SQLite nullable FK columns accept NULL values — a request-level event with `run_id = NULL` satisfies the FK constraint.

**Step 3: Create indexes:**

```sql
CREATE INDEX IF NOT EXISTS idx_events_run    ON events(run_id);
CREATE INDEX IF NOT EXISTS idx_events_trace  ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_job    ON events(job_id);
CREATE INDEX IF NOT EXISTS idx_events_level  ON events(level)
  WHERE level IN ('warning','error');
CREATE INDEX IF NOT EXISTS idx_events_span   ON events(span_type);
```

The partial index on `level` requires SQLite 3.8.0+ (2013). This is safe for all modern Python distributions.

### Event Types by Layer

**Request Events:** `request.received`, `request.completed`, `request.error`, `webhook.delivery.start`, `webhook.delivery.success`, `webhook.delivery.failed`

**Run Events:** `stage.transition`, `gate.waiting`, `gate.approved`, `gate.rejected`, `run.completed`, `run.failed`, `run.cancelled`, `merge.queued`, `merge.completed`, `merge.conflict`

**Job Events:** `job.dispatched`, `job.started`, `job.completed`, `job.failed`, `job.timeout`, `job.interrupted`, `session.created`, `session.closed`, `session.error`, `session.output.warning`

## Diagnostic APIs

### `GET /runs/{run_id}/trace`

**Purpose:** Get complete run lifecycle chain for debugging.

**Query Parameters (all optional):**
- `level` — minimum level filter: `debug|info|warning|error` (default: `info`)
- `span_type` — layer filter: `request|run|job|system`
- `limit` — page size (default: 200, max: 1000)
- `offset` — page offset

**Response:**
```json
{
  "run_id": "run-7f3e",
  "status": "failed",
  "current_stage": "FAILED",
  "failed_at_stage": "DEV_RUNNING",
  "created_at": "2026-03-21T10:00:00Z",
  "summary": {
    "total_events": 12,
    "errors": 2,
    "warnings": 1,
    "stages_visited": ["INIT", "REQ_COLLECTING", "..."],
    "total_duration_ms": 185000,
    "jobs": [
      {"job_id": "job-5c1a", "stage": "DESIGN", "status": "completed", "duration_ms": 35000},
      {"job_id": "job-9a2b", "stage": "DEV", "status": "failed", "duration_ms": 149510}
    ]
  },
  "events": [
    {
      "id": 101,
      "trace_id": "oc-abc123",
      "job_id": null,
      "span_type": "run",
      "event_type": "stage.transition",
      "level": "info",
      "source": "state_machine",
      "payload": {"from": "INIT", "to": "REQ_COLLECTING"},
      "duration_ms": null,
      "error_detail": null,
      "created_at": "2026-03-21T10:00:01.000Z"
    }
  ],
  "pagination": {"limit": 200, "offset": 0, "has_more": false}
}
```

### `GET /jobs/{job_id}/diagnosis`

**Purpose:** Deep-dive into a single job's execution details.

**Data Sources:**

| Response Field | Source |
|---------------|--------|
| `job_id`, `run_id`, `host_id`, `agent_type`, `stage`, `status`, `session_name`, `started_at`, `ended_at` | `jobs` table |
| `diagnosis.duration_ms` | Computed: `ended_at - started_at` |
| `diagnosis.turn_count` | `SELECT COUNT(*) FROM turns WHERE job_id = ?` |
| `diagnosis.error_summary` | Latest `events` row with `job_id = ? AND level = 'error'` → first line of `error_detail` |
| `diagnosis.error_detail` | Latest `events` row with `job_id = ? AND level = 'error'` → full `error_detail` column |
| `diagnosis.last_output_excerpt` | Read last 500 chars from job's `events_file` (NDJSON on disk at `.coop/jobs/{job_id}/events.jsonl`). Returns `null` if file missing. |
| `diagnosis.failure_context.stage_at_failure` | `jobs.stage` column |
| `diagnosis.failure_context.host_status_at_failure` | `SELECT status FROM agent_hosts WHERE id = ?` (live query) |
| `diagnosis.failure_context.retry_count` | `jobs.resume_count` column |
| `events` | `SELECT * FROM events WHERE job_id = ? ORDER BY created_at` |
| `turns` | `SELECT * FROM turns WHERE job_id = ? ORDER BY turn_num` |

**Response:**
```json
{
  "job_id": "job-9a2b",
  "run_id": "run-7f3e",
  "host_id": "dev-01",
  "agent_type": "claude",
  "stage": "DEV_RUNNING",
  "status": "failed",
  "session_name": "run-7f3e-dev",
  "started_at": "2026-03-21T10:00:01.020Z",
  "ended_at": "2026-03-21T10:02:30.510Z",
  "diagnosis": {
    "duration_ms": 149510,
    "turn_count": 3,
    "error_summary": "TimeoutError: acpx session unresponsive after 120s",
    "error_detail": "Traceback (most recent call last):\n  File \"acpx_executor.py\", line 368\n  ...",
    "last_output_excerpt": "Working on file src/auth.py... [truncated]",
    "failure_context": {
      "stage_at_failure": "DEV_RUNNING",
      "host_status_at_failure": "active",
      "retry_count": 0
    }
  },
  "events": [],
  "turns": [
    {"turn_num": 1, "verdict": "needs_retry", "started_at": "...", "ended_at": "..."},
    {"turn_num": 2, "verdict": "needs_retry", "started_at": "...", "ended_at": "..."},
    {"turn_num": 3, "verdict": null, "started_at": "...", "ended_at": null}
  ]
}
```

Note: Reading `events_file` from disk involves file I/O in an API handler. This is acceptable for a diagnostic endpoint (not high-frequency). The read is bounded to the last 500 chars to avoid large reads.

### `GET /traces/{trace_id}`

**Purpose:** Trace a request's full impact across runs and jobs.

**Response:**
```json
{
  "trace_id": "oc-abc123",
  "origin": "external",
  "first_seen": "2026-03-21T10:00:01.000Z",
  "last_seen": "2026-03-21T10:02:30.550Z",
  "total_duration_ms": 149550,
  "affected_runs": ["run-7f3e"],
  "affected_jobs": ["job-9a2b"],
  "error_count": 2,
  "events": []
}
```

### Route Registration

New file `routes/diagnostics.py`:

```python
router = APIRouter(tags=["diagnostics"])

@router.get("/runs/{run_id}/trace")         # full path: /api/v1/runs/{run_id}/trace
@router.get("/jobs/{job_id}/diagnosis")      # full path: /api/v1/jobs/{job_id}/diagnosis
@router.get("/traces/{trace_id}")            # full path: /api/v1/traces/{trace_id}
```

Registered in `app.py` alongside existing routers under the `/api/v1` prefix. No `/diagnostics` prefix — endpoints integrate naturally with existing REST structure (e.g., `/runs/{id}/trace` extends the runs resource).

## Relationship with Existing `_emit_event`

`acpx_executor.py` already has an `_emit_event()` method that writes directly to the `events` table and triggers webhook notifications. The two systems coexist with distinct roles:

| Aspect | `_emit_event` (existing) | `emit_trace_event` (new) |
|--------|-------------------------|--------------------------|
| **Purpose** | Business events that trigger webhooks and state progression | Diagnostic/tracing events for observability |
| **Write mode** | Synchronous DB write (must be committed before webhook fires) | Async queue → batch write (best-effort) |
| **Webhook trigger** | Yes — events like `job.completed` trigger OpenClaw notifications | No — purely for diagnostic queries |
| **Failure impact** | Failure affects business flow | Failure is silently dropped |

**Migration strategy:** `_emit_event` remains unchanged for all events that trigger webhooks or state changes. `emit_trace_event` is used for new diagnostic events (`stage.transition`, `request.*`, `cleanup.*`, `db.*`, etc.) and for enriching error context in existing `except` blocks. The two write to the same `events` table but serve different purposes. No refactoring of `_emit_event` is in scope.

**Event type overlap:** Some event types (e.g., `job.completed`) are emitted by both systems. `_emit_event` records the business event; `emit_trace_event` may add a parallel trace event with additional context (trace_id, duration_ms, error_detail). This duplication is acceptable — diagnostic queries filter by `source` or `trace_id`, and the retention system cleans up old data.

## Exception Handling Reform

### Tiered Strategy

| Level | Scope | Handling |
|-------|-------|---------|
| **error** | Core path: state transitions, job execution, webhook delivery, DB transactions | Catch → emit error event (with stacktrace summary) → continue original logic |
| **debug** | Cleanup path: SSH close, worktree cleanup, PID files | Catch → emit debug event (str(exc) only) → continue silently |

### Transformation Pattern

**Core path — before:**
```python
except asyncio.TimeoutError:
    logger.error("acpx ensure timed out for %s", session_name)
```

**Core path — after:**
```python
except asyncio.TimeoutError:
    logger.error("acpx ensure timed out for %s", session_name)
    await emit_trace_event("session.ensure_timeout", {
        "session_name": session_name,
        "timeout_sec": timeout,
    }, level="error", error_detail=f"TimeoutError: {timeout}s")
```

**Cleanup path — before:**
```python
except Exception:
    pass
```

**Cleanup path — after:**
```python
except Exception as exc:
    await emit_trace_event("cleanup.ssh_close_failed", {
        "host_id": host_id,
    }, level="debug", error_detail=str(exc))
```

### Principles

- Does not change existing control flow — only appends `emit_trace_event()` calls in existing `try/except` blocks
- `format_error(exc, max_lines=10)` — unified exception formatting with truncated stacktrace
- `emit_trace_event` itself never raises (fire-and-forget)
- No new `try/except` blocks added to business logic

### Instrumentation Points

| Module | Instrumentation Locations | Event Types | Levels |
|--------|--------------------------|-------------|--------|
| trace_middleware (NEW) | Request entry/exit | request.received / .completed / .error | info / error |
| state_machine | tick(), on_job_status_changed(), approve/reject | stage.transition / gate.* / run.* | info / error |
| acpx_executor | start_job, watch loop, session lifecycle, cleanup | job.* / session.* / cleanup.* | info / error / debug |
| scheduler | health check, timeout, auto-tick, reminder | health.* / timeout.* / tick.* | info / warning / error |
| webhook_notifier | deliver(), _deliver_openclaw() | webhook.delivery.* | info / error |
| database | _retry_locked_operation(), transaction() | db.lock_retry / db.transaction_failed | warning / error |

### Database Instrumentation: Avoiding Circular Dependency

`database.py` is a low-level module with zero project imports. Adding a direct import of `trace_emitter` would create a circular dependency (`trace_emitter → database` and `database → trace_emitter`).

**Solution:** `database.py` accepts an optional event callback at construction time:

```python
# database.py
class Database:
    def __init__(self, db_path: str, on_trace_event: Callable | None = None):
        self._on_trace_event = on_trace_event

    async def _retry_locked_operation(self, ...):
        ...
        except Exception as exc:
            if self._on_trace_event:
                self._on_trace_event("db.lock_retry", {"attempt": attempt}, "warning", str(exc))
```

```python
# app.py — wiring at startup
db = Database(db_path, on_trace_event=emit_trace_event_sync)
```

This keeps `database.py` dependency-free. The callback is a plain function reference, set once at startup.

## Event Retention

| Data Type | Retention | Description |
|-----------|-----------|-------------|
| Terminal run events | 7 days | After run reaches completed/failed/cancelled |
| debug-level events | 3 days | Low-value cleanup path events recycled early |
| Orphan request events | 3 days | Health checks, non-run API requests |

Scheduler adds a cleanup loop (every 24h). All retention periods configurable via `settings.yaml`.

### Cleanup SQL

```sql
-- 1. Delete events for terminal runs older than retention_days
DELETE FROM events WHERE run_id IN (
  SELECT id FROM runs
  WHERE status IN ('completed', 'failed', 'cancelled')
    AND updated_at < datetime('now', '-7 days')
);

-- 2. Delete debug-level events older than debug_retention_days (regardless of run status)
DELETE FROM events WHERE level = 'debug'
  AND created_at < datetime('now', '-3 days');

-- 3. Delete orphan events (no run association) older than orphan_retention_days
DELETE FROM events WHERE run_id IS NULL
  AND created_at < datetime('now', '-3 days');
```

The cleanup loop runs each query sequentially in the scheduler. Active runs (status = 'running') are never affected — only terminal runs with `updated_at` older than the threshold are cleaned. The `updated_at` column on `runs` serves as the "became terminal" timestamp.

### Configuration

```yaml
tracing:
  enabled: true                    # master switch — false makes emit_trace_event() return immediately
  retention_days: 7                # terminal run event retention
  debug_retention_days: 3          # debug-level event retention
  orphan_retention_days: 3         # no-run event retention
  cleanup_interval_hours: 24       # cleanup loop interval
```

### TracingConfig Pydantic Model

```python
class TracingConfig(BaseModel):
    enabled: bool = True
    retention_days: int = 7
    debug_retention_days: int = 3
    orphan_retention_days: int = 3
    cleanup_interval_hours: int = 24

class Settings(BaseModel):
    server: ServerConfig = ServerConfig()
    database: DatabaseConfig = DatabaseConfig()
    # ... existing fields ...
    tracing: TracingConfig = TracingConfig()   # NEW
```

## Module Overview

### New Files

| File | Responsibility |
|------|---------------|
| `src/trace_context.py` | contextvars management + `get_context()` |
| `src/trace_emitter.py` | `emit_trace_event()` + async queue consumer + `format_error()` |
| `src/trace_middleware.py` | FastAPI middleware: trace_id generation/propagation + request-level events |
| `routes/diagnostics.py` | 3 diagnostic API endpoints |

### Modified Files

| File | Changes |
|------|---------|
| `db/schema.sql` | events table extension + new indexes |
| `src/app.py` | Register middleware + diagnostics router + start trace consumer + start cleanup loop + wire DB callback |
| `src/config.py` | Add `TracingConfig` |
| `src/state_machine.py` | `bind_run()` + stage transition/gate event instrumentation |
| `src/acpx_executor.py` | `bind_job()` + session/job events + silent exception reform |
| `src/scheduler.py` | Internal trace_id + health/timeout events + cleanup loop |
| `src/webhook_notifier.py` | Delivery event instrumentation |
| `src/database.py` | Accept optional `on_trace_event` callback; emit db.lock_retry / db.transaction_failed via callback |

### Dependency Graph

```
trace_context.py  ← zero dependencies, pure contextvars
     ↑
trace_emitter.py  ← depends on trace_context (database handle injected at startup)
     ↑
trace_middleware.py ← depends on trace_context + trace_emitter
     ↑
app.py ← registers middleware, starts consumer, wires callback into database

database.py ← accepts on_trace_event callback (no import of trace_emitter)

state_machine / acpx_executor / scheduler / webhook_notifier
  └── each imports and calls trace_emitter.emit_trace_event() (fire-and-forget)
```

## Testing

### New Module Tests

| Test File | Coverage |
|-----------|----------|
| `tests/test_trace_context.py` | Context propagation across `async` tasks; `bind_run`/`bind_job` nesting; `get_context()` returns correct values; context isolation between concurrent tasks |
| `tests/test_trace_emitter.py` | Fire-and-forget guarantee (never raises); queue overflow behavior (silently drops); consumer batch write; `format_error()` truncation; disabled tracing (`enabled=false`) returns immediately |
| `tests/test_trace_middleware.py` | `X-Trace-Id` header read/write; auto-generation when no header; `request.received`/`request.completed` events emitted; error requests emit `request.error` |
| `tests/test_diagnostics.py` | Integration tests for all 3 diagnostic endpoints; pagination; level/span_type filtering; 404 for missing run/job/trace; diagnosis data source correctness |

### Existing Test Impact

- `emit_trace_event` calls in modified modules are fire-and-forget with `except: pass`. Existing tests should pass without modification.
- For test isolation, `trace_emitter` should be importable with a no-op mode (when no consumer is running, events go to the queue and are never consumed — bounded queue prevents memory issues).
- If existing tests mock `Database`, the `on_trace_event` callback defaults to `None`, so no tracing occurs in tests unless explicitly set up.
