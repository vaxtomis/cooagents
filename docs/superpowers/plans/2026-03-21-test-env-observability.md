# Test Environment Observability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Add three-layer tracing (request/run/job) with diagnostic APIs so openclaw can self-service debug integration issues.

**Architecture:** A `trace_id` propagated via Python `contextvars` links request→run→job events. All trace events flow through an async queue into the existing `events` table (extended with new columns). Three diagnostic API endpoints expose the data for querying.

**Tech Stack:** Python 3.11+, FastAPI, aiosqlite, contextvars, asyncio.Queue

**Spec:** `docs/superpowers/specs/2026-03-21-test-env-observability-design.md`

---

## File Structure

### New Files

| File | Responsibility |
|------|---------------|
| `src/trace_context.py` | `contextvars` management: `new_trace()`, `bind_run()`, `bind_job()`, `get_context()` |
| `src/trace_emitter.py` | `emit_trace_event()`, `emit_trace_event_sync()`, async queue consumer `_trace_consumer()`, `format_error()` |
| `src/trace_middleware.py` | FastAPI middleware: `X-Trace-Id` header handling, request-level event emission |
| `routes/diagnostics.py` | Three diagnostic API endpoints |
| `tests/test_trace_context.py` | Unit tests for context propagation |
| `tests/test_trace_emitter.py` | Unit tests for event emission + consumer |
| `tests/test_trace_middleware.py` | Unit tests for middleware |
| `tests/test_diagnostics.py` | Integration tests for diagnostic APIs |

### Modified Files

| File | Changes |
|------|---------|
| `src/config.py` | Add `TracingConfig` model |
| `db/schema.sql` | Add new columns + indexes to `events` table |
| `src/database.py` | Accept `on_trace_event` callback, add idempotent migration for new columns + nullable `run_id` |
| `src/app.py` | Register middleware, diagnostics router, start trace consumer, wire DB callback |
| `src/state_machine.py` | `bind_run()` + stage transition event instrumentation |
| `src/acpx_executor.py` | `bind_job()` + session/job events + silent exception reform |
| `src/scheduler.py` | Internal `trace_id` + health/timeout events + cleanup loop |
| `src/webhook_notifier.py` | Delivery event instrumentation |

---

## Task 1: TracingConfig

**Files:**
- Modify: `src/config.py:78-86`
- Test: `tests/test_config.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_config.py — append at end
def test_tracing_config_defaults():
    from src.config import Settings
    s = Settings()
    assert s.tracing.enabled is True
    assert s.tracing.retention_days == 7
    assert s.tracing.debug_retention_days == 3
    assert s.tracing.orphan_retention_days == 3
    assert s.tracing.cleanup_interval_hours == 24

def test_tracing_config_from_dict():
    from src.config import Settings
    s = Settings.model_validate({"tracing": {"enabled": False, "retention_days": 14}})
    assert s.tracing.enabled is False
    assert s.tracing.retention_days == 14
    assert s.tracing.debug_retention_days == 3  # default
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_config.py::test_tracing_config_defaults tests/test_config.py::test_tracing_config_from_dict -v`
Expected: FAIL — `Settings` has no `tracing` attribute

- [ ] **Step 3: Implement TracingConfig**

In `src/config.py`, add before the `Settings` class:

```python
class TracingConfig(BaseModel):
    enabled: bool = True
    retention_days: int = 7
    debug_retention_days: int = 3
    orphan_retention_days: int = 3
    cleanup_interval_hours: int = 24
```

Add to `Settings` class body:

```python
    tracing: TracingConfig = TracingConfig()
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_config.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/config.py tests/test_config.py
git commit -m "feat(tracing): add TracingConfig to settings"
```

---

## Task 2: trace_context module

**Files:**
- Create: `src/trace_context.py`
- Create: `tests/test_trace_context.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trace_context.py
import asyncio
import pytest
from src.trace_context import new_trace, bind_run, bind_job, get_context


async def test_new_trace_generates_id():
    token = new_trace()
    ctx = get_context()
    assert ctx["trace_id"] is not None
    assert len(ctx["trace_id"]) == 16
    assert ctx["run_id"] is None
    assert ctx["job_id"] is None
    assert ctx["span_type"] == "request"


async def test_new_trace_with_explicit_id():
    new_trace("my-trace-123")
    ctx = get_context()
    assert ctx["trace_id"] == "my-trace-123"


async def test_bind_run():
    new_trace()
    bind_run("run-abc")
    ctx = get_context()
    assert ctx["run_id"] == "run-abc"
    assert ctx["span_type"] == "run"


async def test_bind_job():
    new_trace()
    bind_run("run-abc")
    bind_job("job-xyz")
    ctx = get_context()
    assert ctx["job_id"] == "job-xyz"
    assert ctx["run_id"] == "run-abc"
    assert ctx["span_type"] == "job"


async def test_context_isolation_between_tasks():
    """Concurrent tasks should have independent contexts."""
    results = {}

    async def task_a():
        new_trace("trace-a")
        bind_run("run-a")
        await asyncio.sleep(0.01)
        results["a"] = get_context()

    async def task_b():
        new_trace("trace-b")
        bind_run("run-b")
        await asyncio.sleep(0.01)
        results["b"] = get_context()

    await asyncio.gather(task_a(), task_b())
    assert results["a"]["trace_id"] == "trace-a"
    assert results["a"]["run_id"] == "run-a"
    assert results["b"]["trace_id"] == "trace-b"
    assert results["b"]["run_id"] == "run-b"


async def test_get_context_returns_empty_when_unset():
    """When no trace has been started, get_context returns safe defaults."""
    # Reset context by running in a fresh task
    result = {}
    async def fresh():
        result.update(get_context())
    await asyncio.create_task(fresh())
    assert result.get("trace_id") is None or result.get("trace_id") == ""
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trace_context.py -v`
Expected: FAIL — `src.trace_context` module not found

- [ ] **Step 3: Implement trace_context**

```python
# src/trace_context.py
"""Async-safe correlation context propagation via contextvars."""
from __future__ import annotations

import uuid
from contextvars import ContextVar

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)
_span_type: ContextVar[str] = ContextVar("span_type", default="request")


def new_trace(trace_id: str | None = None):
    """Generate (or accept) a trace_id and set it in context."""
    tid = trace_id or uuid.uuid4().hex[:16]
    _trace_id.set(tid)
    _run_id.set(None)
    _job_id.set(None)
    _span_type.set("request")
    return tid


def bind_run(run_id: str):
    """Bind a run_id to the current context."""
    _run_id.set(run_id)
    _span_type.set("run")


def bind_job(job_id: str):
    """Bind a job_id to the current context."""
    _job_id.set(job_id)
    _span_type.set("job")


def get_context() -> dict:
    """Return current trace context as a dict. Never raises."""
    try:
        return {
            "trace_id": _trace_id.get(),
            "run_id": _run_id.get(),
            "job_id": _job_id.get(),
            "span_type": _span_type.get(),
        }
    except Exception:
        return {"trace_id": "", "run_id": None, "job_id": None, "span_type": "request"}
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_trace_context.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/trace_context.py tests/test_trace_context.py
git commit -m "feat(tracing): add trace_context module with contextvars propagation"
```

---

## Task 3: trace_emitter module

**Files:**
- Create: `src/trace_emitter.py`
- Create: `tests/test_trace_emitter.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trace_emitter.py
import asyncio
import json
import pytest
from unittest.mock import AsyncMock, MagicMock
from src.trace_context import new_trace, bind_run
from src.trace_emitter import (
    emit_trace_event,
    emit_trace_event_sync,
    format_error,
    TraceEmitter,
)


def test_format_error_basic():
    try:
        raise ValueError("test error")
    except ValueError as exc:
        result = format_error(exc)
    assert "ValueError" in result
    assert "test error" in result


def test_format_error_truncates():
    try:
        raise ValueError("x" * 500)
    except ValueError as exc:
        result = format_error(exc, max_lines=3)
    lines = result.strip().splitlines()
    assert len(lines) <= 5  # 3 head + truncated marker + 1 tail at most


async def test_emitter_writes_to_db():
    db = AsyncMock()
    emitter = TraceEmitter(db, enabled=True)
    consumer_task = asyncio.create_task(emitter.start_consumer())

    new_trace("test-trace")
    bind_run("run-001")
    await emitter.emit("stage.transition", {"from": "INIT", "to": "REQ_COLLECTING"})
    await asyncio.sleep(0.1)  # let consumer drain

    emitter.stop()
    await consumer_task

    db.execute.assert_called()
    call_args = db.execute.call_args
    sql = call_args[0][0]
    assert "INSERT INTO events" in sql
    params = call_args[0][1]
    assert "test-trace" in params  # trace_id in params


async def test_emitter_never_raises():
    """emit must never raise even if everything fails."""
    emitter = TraceEmitter(None, enabled=True)
    # Should not raise even with no DB, no consumer
    await emitter.emit("test.event", {"key": "val"})
    emitter.emit_sync("test.event", {"key": "val"})


async def test_emitter_disabled():
    db = AsyncMock()
    emitter = TraceEmitter(db, enabled=False)
    await emitter.emit("test.event")
    db.execute.assert_not_called()


def test_emit_sync_never_raises():
    emitter = TraceEmitter(None, enabled=True)
    emitter.emit_sync("test.event", {"key": "val"})
    # No exception = pass
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trace_emitter.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement trace_emitter**

```python
# src/trace_emitter.py
"""Fire-and-forget trace event emitter with async queue consumer."""
from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone
from typing import Any

from src.trace_context import get_context

logger = logging.getLogger(__name__)


def format_error(exc: Exception, max_lines: int = 10) -> str:
    """Format exception for error_detail field. Truncates long tracebacks."""
    tb = traceback.format_exception(type(exc), exc, exc.__traceback__)
    lines = "".join(tb).strip().splitlines()
    if len(lines) > max_lines:
        lines = lines[:3] + ["  ... truncated ..."] + lines[-(max_lines - 4):]
    return "\n".join(lines)


class TraceEmitter:
    """Manages trace event emission and background consumption."""

    def __init__(self, db, enabled: bool = True):
        self._db = db
        self._enabled = enabled
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        self._running = False

    async def emit(
        self,
        event_type: str,
        payload: dict | None = None,
        level: str = "info",
        error_detail: str | None = None,
        duration_ms: int | None = None,
        source: str | None = None,
    ):
        """Fire-and-forget async emit. Never raises."""
        if not self._enabled:
            return
        try:
            ctx = get_context()
            self._queue.put_nowait((
                event_type, ctx, payload, level, error_detail, duration_ms, source,
            ))
        except (asyncio.QueueFull, Exception):
            pass

    def emit_sync(
        self,
        event_type: str,
        payload: dict | None = None,
        level: str = "info",
        error_detail: str | None = None,
        duration_ms: int | None = None,
        source: str | None = None,
    ):
        """Synchronous variant for use as database.py callback. Never raises."""
        if not self._enabled:
            return
        try:
            ctx = get_context()
            self._queue.put_nowait((
                event_type, ctx, payload, level, error_detail, duration_ms, source,
            ))
        except (asyncio.QueueFull, Exception):
            pass

    async def start_consumer(self):
        """Background task that drains the queue and batch-writes to DB."""
        self._running = True
        while self._running:
            try:
                batch = []
                item = await asyncio.wait_for(self._queue.get(), timeout=1.0)
                batch.append(item)
                while len(batch) < 64:
                    try:
                        batch.append(self._queue.get_nowait())
                    except asyncio.QueueEmpty:
                        break
                await self._write_batch(batch)
            except asyncio.TimeoutError:
                continue
            except asyncio.CancelledError:
                break
            except Exception:
                logger.warning("trace consumer: batch write failed, %d events dropped", len(batch) if 'batch' in dir() else 0)

        # Drain remaining on shutdown
        await self._drain_remaining()

    def stop(self):
        self._running = False

    async def _drain_remaining(self):
        """Drain remaining items on shutdown."""
        batch = []
        while not self._queue.empty():
            try:
                batch.append(self._queue.get_nowait())
            except asyncio.QueueEmpty:
                break
        if batch and self._db:
            try:
                await self._write_batch(batch)
            except Exception:
                pass

    async def _write_batch(self, batch: list):
        if not self._db or not batch:
            return
        now = datetime.now(timezone.utc).isoformat()
        for item in batch:
            event_type, ctx, payload, level, error_detail, duration_ms, source = item
            await self._db.execute(
                "INSERT INTO events (run_id, event_type, payload_json, created_at, "
                "trace_id, job_id, span_type, level, duration_ms, error_detail, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ctx.get("run_id"),
                    event_type,
                    json.dumps(payload) if payload else None,
                    now,
                    ctx.get("trace_id"),
                    ctx.get("job_id"),
                    ctx.get("span_type", "system"),
                    level,
                    duration_ms,
                    error_detail,
                    source,
                ),
            )
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_trace_emitter.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/trace_emitter.py tests/test_trace_emitter.py
git commit -m "feat(tracing): add trace_emitter with async queue consumer"
```

---

## Task 4: Database schema migration

**Files:**
- Modify: `db/schema.sql:35-41`
- Modify: `src/database.py:25,71-76`
- Test: `tests/test_database.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/test_database.py — append at end
async def test_events_table_has_trace_columns(db):
    """After connect, events table should have tracing columns."""
    row = await db.fetchone(
        "SELECT sql FROM sqlite_master WHERE type='table' AND name='events'"
    )
    sql = row["sql"]
    assert "trace_id" in sql
    assert "job_id" in sql
    assert "span_type" in sql
    assert "level" in sql
    assert "duration_ms" in sql
    assert "error_detail" in sql
    assert "source" in sql


async def test_events_run_id_nullable(db):
    """run_id should be nullable for request-level events."""
    await db.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at,trace_id,span_type,level,source) "
        "VALUES(NULL,'request.received',NULL,datetime('now'),'abc123','request','info','middleware')"
    )
    row = await db.fetchone("SELECT * FROM events WHERE trace_id='abc123'")
    assert row is not None
    assert row["run_id"] is None


async def test_database_on_trace_event_callback(db):
    """Database should accept and call on_trace_event callback."""
    calls = []
    def on_event(event_type, payload, level, error_detail):
        calls.append((event_type, payload, level, error_detail))

    # Create a new DB instance with callback
    db2 = Database(db_path=db._db_path, schema_path="db/schema.sql", on_trace_event=on_event)
    await db2.connect()
    # The callback is just stored, not tested via lock retry here
    assert db2._on_trace_event is on_event
    await db2.close()
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_database.py::test_events_table_has_trace_columns tests/test_database.py::test_events_run_id_nullable tests/test_database.py::test_database_on_trace_event_callback -v`
Expected: FAIL — columns don't exist, callback param not accepted

- [ ] **Step 3: Update schema.sql**

Replace the events table definition in `db/schema.sql` (lines 34-41):

```sql
-- 3. events — audit log with tracing support
CREATE TABLE IF NOT EXISTS events (
  id           INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id       TEXT REFERENCES runs(id),
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
```

Update the indexes section at the bottom of `schema.sql` — add after existing `idx_events_run`:

```sql
CREATE INDEX IF NOT EXISTS idx_events_trace  ON events(trace_id);
CREATE INDEX IF NOT EXISTS idx_events_job    ON events(job_id);
CREATE INDEX IF NOT EXISTS idx_events_level  ON events(level) WHERE level IN ('warning','error');
CREATE INDEX IF NOT EXISTS idx_events_span   ON events(span_type);
```

- [ ] **Step 4: Update database.py**

Modify `Database.__init__` to accept callback:

```python
def __init__(self, db_path: str | Path, schema_path: str | Path, on_trace_event=None) -> None:
    # ... existing code ...
    self._on_trace_event = on_trace_event
```

Add to `_apply_compat_migrations` — new column checks and nullable migration:

```python
async def _apply_compat_migrations(self) -> None:
    conn = self._ensure_connected()
    if not await self._column_exists("jobs", "timeout_sec"):
        await conn.execute("ALTER TABLE jobs ADD COLUMN timeout_sec INTEGER")
    if not await self._column_exists("jobs", "running_started_at"):
        await conn.execute("ALTER TABLE jobs ADD COLUMN running_started_at TEXT")

    # Tracing columns migration
    trace_cols = {
        "trace_id": "TEXT",
        "job_id": "TEXT",
        "span_type": "TEXT DEFAULT 'system'",
        "level": "TEXT DEFAULT 'info'",
        "duration_ms": "INTEGER",
        "error_detail": "TEXT",
        "source": "TEXT",
    }
    for col, col_type in trace_cols.items():
        if not await self._column_exists("events", col):
            await conn.execute(f"ALTER TABLE events ADD COLUMN {col} {col_type}")

    # Make run_id nullable: check notnull flag via PRAGMA
    async with conn.execute("PRAGMA table_info(events)") as cursor:
        rows = await cursor.fetchall()
    for row in rows:
        if row["name"] == "run_id" and row["notnull"] == 1:
            await self._migrate_events_nullable_run_id(conn)
            break

    # Ensure tracing indexes exist
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_trace ON events(trace_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_job ON events(job_id)")
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_span ON events(span_type)")
    try:
        await conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_events_level ON events(level) "
            "WHERE level IN ('warning','error')"
        )
    except Exception:
        await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_level ON events(level)")
```

Add the migration helper:

```python
async def _migrate_events_nullable_run_id(self, conn) -> None:
    """Rebuild events table to make run_id nullable."""
    await conn.execute("""
        CREATE TABLE IF NOT EXISTS events_new (
            id           INTEGER PRIMARY KEY AUTOINCREMENT,
            run_id       TEXT REFERENCES runs(id),
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
        )
    """)
    # Copy existing data — use column names explicitly to handle both old and new schemas
    existing_cols = ["id", "run_id", "event_type", "payload_json", "created_at"]
    for col in ["trace_id", "job_id", "span_type", "level", "duration_ms", "error_detail", "source"]:
        if await self._column_exists("events", col):
            existing_cols.append(col)
    cols = ", ".join(existing_cols)
    await conn.execute(f"INSERT INTO events_new ({cols}) SELECT {cols} FROM events")
    await conn.execute("DROP TABLE events")
    await conn.execute("ALTER TABLE events_new RENAME TO events")
    # Re-create the run_id index
    await conn.execute("CREATE INDEX IF NOT EXISTS idx_events_run ON events(run_id)")
```

Also add callback usage in `_retry_locked_operation`:

```python
async def _retry_locked_operation(self, operation):
    attempts = 1 if self._in_transaction else self._LOCK_RETRY_ATTEMPTS
    for attempt in range(attempts):
        try:
            return await operation()
        except sqlite3.OperationalError as exc:
            if attempt == attempts - 1 or not self._is_locked_error(exc):
                raise
            if self._on_trace_event:
                self._on_trace_event(
                    "db.lock_retry",
                    {"attempt": attempt + 1, "max_attempts": attempts},
                    "warning",
                    str(exc),
                )
            await asyncio.sleep(self._LOCK_RETRY_BASE_DELAY_SEC * (2 ** attempt))
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `python -m pytest tests/test_database.py -v`
Expected: ALL PASS

- [ ] **Step 6: Commit**

```bash
git add db/schema.sql src/database.py tests/test_database.py
git commit -m "feat(tracing): extend events table schema + nullable run_id migration"
```

---

## Task 5: Trace middleware

**Files:**
- Create: `src/trace_middleware.py`
- Create: `tests/test_trace_middleware.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_trace_middleware.py
import asyncio
import pytest
from unittest.mock import AsyncMock, MagicMock
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.trace_middleware import TraceMiddleware
from src.trace_emitter import TraceEmitter


def _make_app(emitter=None):
    app = FastAPI()
    emitter = emitter or TraceEmitter(None, enabled=False)
    app.add_middleware(TraceMiddleware, emitter=emitter)

    @app.get("/test")
    async def test_endpoint():
        return {"ok": True}

    @app.get("/error")
    async def error_endpoint():
        raise ValueError("boom")

    return app


def test_middleware_adds_trace_id_header():
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/test")
    assert resp.status_code == 200
    assert "x-trace-id" in resp.headers


def test_middleware_uses_provided_trace_id():
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/test", headers={"X-Trace-Id": "my-trace"})
    assert resp.headers["x-trace-id"] == "my-trace"


def test_middleware_generates_trace_id_when_missing():
    app = _make_app()
    client = TestClient(app)
    resp = client.get("/test")
    trace_id = resp.headers.get("x-trace-id")
    assert trace_id is not None
    assert len(trace_id) == 16
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_trace_middleware.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement trace_middleware**

```python
# src/trace_middleware.py
"""FastAPI middleware for trace_id generation and request-level event emission."""
from __future__ import annotations

import time
from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from src.trace_context import new_trace, get_context


class TraceMiddleware(BaseHTTPMiddleware):
    def __init__(self, app, emitter=None):
        super().__init__(app)
        self._emitter = emitter

    async def dispatch(self, request: Request, call_next):
        # Read or generate trace_id
        trace_id = request.headers.get("x-trace-id") or None
        trace_id = new_trace(trace_id)

        start_time = time.monotonic()

        # Emit request.received
        if self._emitter:
            await self._emitter.emit(
                "request.received",
                {"method": request.method, "path": str(request.url.path)},
                source="middleware",
            )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            if self._emitter:
                await self._emitter.emit(
                    "request.error",
                    {"method": request.method, "path": str(request.url.path), "error": str(exc)[:200]},
                    level="error",
                    error_detail=str(exc),
                    duration_ms=duration_ms,
                    source="middleware",
                )
            raise

        duration_ms = int((time.monotonic() - start_time) * 1000)
        response.headers["x-trace-id"] = trace_id

        # Emit request.completed
        if self._emitter:
            await self._emitter.emit(
                "request.completed",
                {"method": request.method, "path": str(request.url.path), "status": response.status_code},
                duration_ms=duration_ms,
                source="middleware",
            )

        return response
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_trace_middleware.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/trace_middleware.py tests/test_trace_middleware.py
git commit -m "feat(tracing): add TraceMiddleware for request-level tracing"
```

---

## Task 6: Diagnostic APIs

**Files:**
- Create: `routes/diagnostics.py`
- Create: `tests/test_diagnostics.py`

- [ ] **Step 1: Write the failing tests**

```python
# tests/test_diagnostics.py
import json
import pytest
from datetime import datetime, timezone
from fastapi import FastAPI
from fastapi.testclient import TestClient
from src.database import Database

@pytest.fixture
async def db(tmp_path):
    d = Database(db_path=tmp_path / "test.db", schema_path="db/schema.sql")
    await d.connect()
    yield d
    await d.close()

@pytest.fixture
def app(db):
    from routes.diagnostics import create_diagnostics_router
    app = FastAPI()
    router = create_diagnostics_router(db)
    app.include_router(router)
    return app

@pytest.fixture
def client(app):
    return TestClient(app)


async def test_run_trace_returns_events(db, client):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-1", "TKT-1", "/repo", "failed", "FAILED", now, now),
    )
    await db.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at,trace_id,span_type,level,source) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("run-1", "stage.transition", '{"from":"INIT","to":"REQ_COLLECTING"}', now, "t1", "run", "info", "state_machine"),
    )
    resp = client.get("/runs/run-1/trace")
    assert resp.status_code == 200
    data = resp.json()
    assert data["run_id"] == "run-1"
    assert len(data["events"]) == 1
    assert "summary" in data


async def test_run_trace_404(client):
    resp = client.get("/runs/nonexistent/trace")
    assert resp.status_code == 404


async def test_job_diagnosis_returns_data(db, client):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-1", "TKT-1", "/repo", "failed", "FAILED", now, now),
    )
    await db.execute(
        "INSERT INTO jobs(id,run_id,agent_type,stage,status,started_at) VALUES(?,?,?,?,?,?)",
        ("job-1", "run-1", "claude", "DEV_RUNNING", "failed", now),
    )
    resp = client.get("/jobs/job-1/diagnosis")
    assert resp.status_code == 200
    data = resp.json()
    assert data["job_id"] == "job-1"
    assert "diagnosis" in data


async def test_trace_lookup(db, client):
    now = datetime.now(timezone.utc).isoformat()
    await db.execute(
        "INSERT INTO runs(id,ticket,repo_path,status,current_stage,created_at,updated_at) "
        "VALUES(?,?,?,?,?,?,?)",
        ("run-1", "TKT-1", "/repo", "running", "INIT", now, now),
    )
    await db.execute(
        "INSERT INTO events(run_id,event_type,payload_json,created_at,trace_id,span_type,level,source) "
        "VALUES(?,?,?,?,?,?,?,?)",
        ("run-1", "request.received", None, now, "trace-abc", "request", "info", "middleware"),
    )
    resp = client.get("/traces/trace-abc")
    assert resp.status_code == 200
    data = resp.json()
    assert data["trace_id"] == "trace-abc"
    assert "run-1" in data["affected_runs"]
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `python -m pytest tests/test_diagnostics.py -v`
Expected: FAIL — module not found

- [ ] **Step 3: Implement diagnostics router**

```python
# routes/diagnostics.py
"""Diagnostic API endpoints for trace querying."""
from __future__ import annotations

import json
from fastapi import APIRouter, Query, Request
from fastapi.responses import JSONResponse


def create_diagnostics_router(db=None):
    router = APIRouter(tags=["diagnostics"])

    def _get_db(request: Request = None):
        if db is not None:
            return db
        return request.app.state.db

    @router.get("/runs/{run_id}/trace")
    async def run_trace(
        request: Request,
        run_id: str,
        level: str = Query("info", regex="^(debug|info|warning|error)$"),
        span_type: str | None = Query(None),
        limit: int = Query(200, le=1000),
        offset: int = Query(0, ge=0),
    ):
        d = _get_db(request)
        run = await d.fetchone("SELECT * FROM runs WHERE id=?", (run_id,))
        if not run:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": f"Run {run_id} not found"})

        level_order = {"debug": 0, "info": 1, "warning": 2, "error": 3}
        min_level = level_order.get(level, 1)

        # Build query
        conditions = ["run_id = ?"]
        params = [run_id]

        levels_included = [l for l, v in level_order.items() if v >= min_level]
        placeholders = ",".join("?" * len(levels_included))
        conditions.append(f"level IN ({placeholders})")
        params.extend(levels_included)

        if span_type:
            conditions.append("span_type = ?")
            params.append(span_type)

        where = " AND ".join(conditions)

        # Total count
        count_row = await d.fetchone(f"SELECT COUNT(*) as c FROM events WHERE {where}", tuple(params))
        total = count_row["c"] if count_row else 0

        # Fetch events
        params_with_pagination = list(params) + [limit, offset]
        events = await d.fetchall(
            f"SELECT * FROM events WHERE {where} ORDER BY created_at, id LIMIT ? OFFSET ?",
            tuple(params_with_pagination),
        )

        # Parse payload_json for response
        for evt in events:
            if evt.get("payload_json"):
                try:
                    evt["payload"] = json.loads(evt["payload_json"])
                except (json.JSONDecodeError, TypeError):
                    evt["payload"] = evt["payload_json"]
            else:
                evt["payload"] = None
            evt.pop("payload_json", None)

        # Build summary
        jobs = await d.fetchall(
            "SELECT id, stage, status, started_at, ended_at FROM jobs WHERE run_id=? ORDER BY started_at",
            (run_id,),
        )
        steps = await d.fetchall(
            "SELECT to_stage FROM steps WHERE run_id=? ORDER BY created_at", (run_id,)
        )
        stages_visited = [s["to_stage"] for s in steps]

        error_count = await d.fetchone(
            "SELECT COUNT(*) as c FROM events WHERE run_id=? AND level='error'", (run_id,)
        )
        warn_count = await d.fetchone(
            "SELECT COUNT(*) as c FROM events WHERE run_id=? AND level='warning'", (run_id,)
        )

        job_summaries = []
        for j in jobs:
            duration = None
            if j.get("started_at") and j.get("ended_at"):
                from datetime import datetime
                try:
                    s = datetime.fromisoformat(j["started_at"])
                    e = datetime.fromisoformat(j["ended_at"])
                    duration = int((e - s).total_seconds() * 1000)
                except Exception:
                    pass
            job_summaries.append({
                "job_id": j["id"],
                "stage": j["stage"],
                "status": j["status"],
                "duration_ms": duration,
            })

        return {
            "run_id": run_id,
            "status": run["status"],
            "current_stage": run["current_stage"],
            "failed_at_stage": run.get("failed_at_stage"),
            "created_at": run["created_at"],
            "summary": {
                "total_events": total,
                "errors": error_count["c"] if error_count else 0,
                "warnings": warn_count["c"] if warn_count else 0,
                "stages_visited": stages_visited,
                "jobs": job_summaries,
            },
            "events": events,
            "pagination": {"limit": limit, "offset": offset, "has_more": (offset + limit) < total},
        }

    @router.get("/jobs/{job_id}/diagnosis")
    async def job_diagnosis(request: Request, job_id: str, level: str = Query("info")):
        d = _get_db(request)
        job = await d.fetchone("SELECT * FROM jobs WHERE id=?", (job_id,))
        if not job:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": f"Job {job_id} not found"})

        job = dict(job)

        # Duration
        duration_ms = None
        if job.get("started_at") and job.get("ended_at"):
            from datetime import datetime
            try:
                s = datetime.fromisoformat(job["started_at"])
                e = datetime.fromisoformat(job["ended_at"])
                duration_ms = int((e - s).total_seconds() * 1000)
            except Exception:
                pass

        # Turn count + turns
        turns = await d.fetchall("SELECT * FROM turns WHERE job_id=? ORDER BY turn_num", (job_id,))
        turn_count = len(turns)

        # Error info from events
        error_event = await d.fetchone(
            "SELECT error_detail FROM events WHERE job_id=? AND level='error' ORDER BY created_at DESC LIMIT 1",
            (job_id,),
        )
        error_detail = error_event["error_detail"] if error_event else None
        error_summary = error_detail.split("\n")[-1] if error_detail else None

        # Last output excerpt from events_file
        last_output = None
        events_file = job.get("events_file")
        if events_file:
            try:
                import os
                if os.path.exists(events_file):
                    with open(events_file, "r", encoding="utf-8", errors="replace") as f:
                        f.seek(0, 2)
                        size = f.tell()
                        read_start = max(0, size - 500)
                        f.seek(read_start)
                        last_output = f.read()
            except Exception:
                pass

        # Host status at failure
        host_status = None
        if job.get("host_id"):
            host = await d.fetchone("SELECT status FROM agent_hosts WHERE id=?", (job["host_id"],))
            host_status = host["status"] if host else None

        # Events for this job
        events = await d.fetchall(
            "SELECT * FROM events WHERE job_id=? ORDER BY created_at", (job_id,)
        )
        for evt in events:
            if evt.get("payload_json"):
                try:
                    evt["payload"] = json.loads(evt["payload_json"])
                except Exception:
                    evt["payload"] = evt["payload_json"]
            else:
                evt["payload"] = None
            evt.pop("payload_json", None)

        return {
            "job_id": job_id,
            "run_id": job.get("run_id"),
            "host_id": job.get("host_id"),
            "agent_type": job.get("agent_type"),
            "stage": job.get("stage"),
            "status": job.get("status"),
            "session_name": job.get("session_name"),
            "started_at": job.get("started_at"),
            "ended_at": job.get("ended_at"),
            "diagnosis": {
                "duration_ms": duration_ms,
                "turn_count": turn_count,
                "error_summary": error_summary,
                "error_detail": error_detail,
                "last_output_excerpt": last_output,
                "failure_context": {
                    "stage_at_failure": job.get("stage"),
                    "host_status_at_failure": host_status,
                    "retry_count": job.get("resume_count", 0),
                },
            },
            "events": events,
            "turns": [dict(t) for t in turns],
        }

    @router.get("/traces/{trace_id}")
    async def trace_lookup(request: Request, trace_id: str, level: str = Query("info")):
        d = _get_db(request)

        events = await d.fetchall(
            "SELECT * FROM events WHERE trace_id=? ORDER BY created_at, id", (trace_id,)
        )
        if not events:
            return JSONResponse(status_code=404, content={"error": "not_found", "message": f"Trace {trace_id} not found"})

        for evt in events:
            if evt.get("payload_json"):
                try:
                    evt["payload"] = json.loads(evt["payload_json"])
                except Exception:
                    evt["payload"] = evt["payload_json"]
            else:
                evt["payload"] = None
            evt.pop("payload_json", None)

        affected_runs = list({e["run_id"] for e in events if e.get("run_id")})
        affected_jobs = list({e["job_id"] for e in events if e.get("job_id")})
        error_count = sum(1 for e in events if e.get("level") == "error")

        first_seen = events[0]["created_at"] if events else None
        last_seen = events[-1]["created_at"] if events else None

        # Determine origin from trace_id prefix
        origin = "scheduler" if trace_id.startswith("sched-") else "external" if "-" in trace_id else "internal"

        return {
            "trace_id": trace_id,
            "origin": origin,
            "first_seen": first_seen,
            "last_seen": last_seen,
            "affected_runs": affected_runs,
            "affected_jobs": affected_jobs,
            "error_count": error_count,
            "events": events,
        }

    return router
```

- [ ] **Step 4: Run tests to verify they pass**

Run: `python -m pytest tests/test_diagnostics.py -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add routes/diagnostics.py tests/test_diagnostics.py
git commit -m "feat(tracing): add diagnostic API endpoints for run/job/trace queries"
```

---

## Task 7: Wire everything in app.py

**Files:**
- Modify: `src/app.py`

- [ ] **Step 1: Update app.py lifespan**

Add imports at top of `src/app.py`:

```python
from src.trace_emitter import TraceEmitter
from src.trace_middleware import TraceMiddleware
```

In `lifespan()`, after `db = Database(...)` and `await db.connect()`, add:

```python
    # Tracing infrastructure
    trace_emitter = TraceEmitter(db, enabled=settings.tracing.enabled)
    consumer_task = asyncio.create_task(trace_emitter.start_consumer()) if settings.tracing.enabled else None
```

Modify Database construction to pass callback:

```python
    db = Database(
        db_path=settings.database.path,
        schema_path="db/schema.sql",
        on_trace_event=trace_emitter.emit_sync if settings.tracing.enabled else None,
    )
```

Note: `TraceEmitter` must be created before `Database` to pass the callback, but it needs `db` for the consumer. Wire it as:

```python
    trace_emitter = TraceEmitter(None, enabled=settings.tracing.enabled)
    db = Database(
        db_path=settings.database.path,
        schema_path="db/schema.sql",
        on_trace_event=trace_emitter.emit_sync if settings.tracing.enabled else None,
    )
    await db.connect()
    trace_emitter._db = db  # wire after connect
    consumer_task = asyncio.create_task(trace_emitter.start_consumer()) if settings.tracing.enabled else None
```

Store on app state:

```python
    app.state.trace_emitter = trace_emitter
```

In shutdown (after `await scheduler.stop()`, before `await db.close()`):

```python
    if consumer_task:
        trace_emitter.stop()
        try:
            await asyncio.wait_for(consumer_task, timeout=3.0)
        except (asyncio.TimeoutError, asyncio.CancelledError):
            pass
```

- [ ] **Step 2: Register middleware and diagnostics router**

After `app = FastAPI(...)`, add middleware:

```python
# Middleware is added later in lifespan via app state, but Starlette requires it at init time.
# Use a lazy pattern: middleware checks app.state for emitter.
```

Actually, add middleware right after app creation using a simple approach:

```python
app.add_middleware(TraceMiddleware, emitter=None)  # emitter set dynamically
```

But since middleware needs the emitter at request time and it's not available at import time, modify `TraceMiddleware.dispatch` to fall back to `request.app.state.trace_emitter`.

Add router imports at bottom of app.py:

```python
from routes.diagnostics import create_diagnostics_router
# Create router at module level with db=None — it will use request.app.state.db at runtime
app.include_router(create_diagnostics_router(), prefix="/api/v1")
```

- [ ] **Step 3: Run existing tests to verify nothing breaks**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/app.py
git commit -m "feat(tracing): wire trace emitter, middleware, and diagnostics into app"
```

---

## Task 8: Instrument state_machine.py

**Files:**
- Modify: `src/state_machine.py`

- [ ] **Step 1: Add trace instrumentation**

At top of `state_machine.py`, add import:

```python
from src.trace_context import bind_run
from src.trace_emitter import TraceEmitter
```

Store emitter reference — update `__init__` to accept optional `trace_emitter`:

```python
    def __init__(self, ..., trace_emitter=None):
        # ... existing code ...
        self._trace = trace_emitter
```

Add helper method:

```python
    async def _trace_event(self, event_type, payload=None, level="info", error_detail=None, duration_ms=None):
        if self._trace:
            await self._trace.emit(event_type, payload, level=level, error_detail=error_detail,
                                   duration_ms=duration_ms, source="state_machine")
```

Instrument `_update_stage` method (the central stage transition method). Find it and add after the stage update:

```python
    async def _update_stage(self, run_id, from_stage, to_stage):
        # ... existing UPDATE SQL ...
        bind_run(run_id)
        await self._trace_event("stage.transition", {"from": from_stage, "to": to_stage})
```

Instrument `approve`:

```python
    # After the existing _emit call for gate.approved
    await self._trace_event("gate.approved", {"gate": gate, "by": by})
```

Instrument `reject`:

```python
    await self._trace_event("gate.rejected", {"gate": gate, "by": by, "reason": reason})
```

Instrument failure path — find where run status is set to "failed" and add:

```python
    await self._trace_event("run.failed", {"failed_at_stage": current_stage}, level="error")
```

- [ ] **Step 2: Update app.py to pass trace_emitter to StateMachine**

In `app.py`, modify the StateMachine construction:

```python
    sm = StateMachine(
        db, artifacts, hosts, executor, webhooks, merger,
        coop_dir=coop_dir, config=settings, job_manager=jobs, project_root=project_root,
        trace_emitter=trace_emitter,
    )
```

- [ ] **Step 3: Run existing state_machine tests**

Run: `python -m pytest tests/test_state_machine.py -v`
Expected: ALL PASS (trace_emitter defaults to None, so existing tests unaffected)

- [ ] **Step 4: Commit**

```bash
git add src/state_machine.py src/app.py
git commit -m "feat(tracing): instrument state_machine with stage transition events"
```

---

## Task 9: Instrument acpx_executor.py

**Files:**
- Modify: `src/acpx_executor.py`

- [ ] **Step 1: Add trace instrumentation**

At top, add imports:

```python
from src.trace_context import bind_run, bind_job
from src.trace_emitter import TraceEmitter, format_error
```

Add `trace_emitter` parameter to `__init__`, store as `self._trace`. Add helper:

```python
    async def _trace_event(self, event_type, payload=None, level="info", error_detail=None, duration_ms=None):
        if self._trace:
            await self._trace.emit(event_type, payload, level=level, error_detail=error_detail,
                                   duration_ms=duration_ms, source="acpx")
```

**Core path instrumentation** — add `await self._trace_event(...)` calls in these existing `except` blocks:

- Line 52-53 (job status callback failed): already has logger.error — add trace event with `format_error(exc)`
- Lines 368-378 (session ensure timeout): add `await self._trace_event("session.ensure_timeout", {...}, level="error", error_detail=...)`
- Lines 399-403 (startup failed): add `await self._trace_event("session.ensure_failed", {...}, level="error", error_detail=format_error(exc))`
- Lines 723-726 (watch loop exception): add `await self._trace_event("job.error", {...}, level="error", error_detail=format_error(e))`

**Cleanup path instrumentation** — change `except: pass` to `except: emit debug`:

- Line 452-453: `await self._trace_event("cleanup.ssh_close", ..., level="debug", error_detail=str(exc))`
- Line 474-475: `await self._trace_event("cleanup.session_close", ..., level="debug", ...)`
- Lines 495, 524, 542, 560, 578: same pattern for each cleanup block
- Lines 739-740, 745-746: SSH resource cleanup

**Job lifecycle instrumentation** — add `bind_job(job_id)` when starting a job, and trace events for key lifecycle points (job.dispatched, job.started, session.created).

- [ ] **Step 2: Update app.py to pass trace_emitter to AcpxExecutor**

```python
    executor = AcpxExecutor(
        db, jobs, hosts, artifacts, webhooks,
        config=settings, coop_dir=coop_dir, project_root=project_root,
        trace_emitter=trace_emitter,
    )
```

- [ ] **Step 3: Run existing acpx_executor tests**

Run: `python -m pytest tests/test_acpx_executor.py -v`
Expected: ALL PASS

- [ ] **Step 4: Commit**

```bash
git add src/acpx_executor.py src/app.py
git commit -m "feat(tracing): instrument acpx_executor with job/session events and exception reform"
```

---

## Task 10: Instrument scheduler.py and webhook_notifier.py

**Files:**
- Modify: `src/scheduler.py`
- Modify: `src/webhook_notifier.py`

- [ ] **Step 1: Instrument scheduler.py**

Add imports:

```python
from src.trace_context import new_trace, bind_run
from src.trace_emitter import TraceEmitter, format_error
```

Add `trace_emitter` param to `__init__`, store as `self._trace`. Add helper as in previous tasks.

In each background loop, at the start of each iteration, generate an internal trace_id:

```python
    async def _health_check_loop(self):
        while True:
            try:
                await asyncio.sleep(self.config.health_check.interval)
                new_trace(f"sched-health-{...}")  # use uuid hex
                # ... existing code ...
```

Instrument existing `except` blocks with `await self._trace_event(...)`:

- Line 49: health check error
- Line 70: starting job timeout error
- Line 91: running job timeout error
- Line 95: timeout enforcement error
- Line 102: auto-tick error
- Line 127: reminder loop error
- Line 142: auto-tick run error

Add cleanup loop for event retention:

```python
    async def _event_cleanup_loop(self):
        while True:
            try:
                await asyncio.sleep(self.config.tracing.cleanup_interval_hours * 3600)
                new_trace(f"sched-cleanup-{uuid.uuid4().hex[:8]}")
                await self._cleanup_old_events()
            except asyncio.CancelledError:
                raise
            except Exception as e:
                logger.error(f"Event cleanup error: {e}")

    async def _cleanup_old_events(self):
        cfg = self.config.tracing
        # Terminal run events
        await self.db.execute(
            "DELETE FROM events WHERE run_id IN "
            "(SELECT id FROM runs WHERE status IN ('completed','failed','cancelled') "
            "AND updated_at < datetime('now', ?))",
            (f"-{cfg.retention_days} days",),
        )
        # Debug events
        await self.db.execute(
            "DELETE FROM events WHERE level='debug' AND created_at < datetime('now', ?)",
            (f"-{cfg.debug_retention_days} days",),
        )
        # Orphan events
        await self.db.execute(
            "DELETE FROM events WHERE run_id IS NULL AND created_at < datetime('now', ?)",
            (f"-{cfg.orphan_retention_days} days",),
        )
```

Start the cleanup loop in `start()`:

```python
    async def start(self):
        # ... existing tasks ...
        if hasattr(self.config, 'tracing') and self.config.tracing.enabled:
            self._tasks.append(asyncio.create_task(self._event_cleanup_loop()))
```

- [ ] **Step 2: Instrument webhook_notifier.py**

Add import:

```python
from src.trace_emitter import TraceEmitter
```

Add `trace_emitter` param to `__init__`, store as `self._trace`. Add helper.

Instrument `_deliver_to_openclaw` — add trace events for delivery attempts:

```python
    # After successful delivery return
    await self._trace_event("webhook.delivery.success", {"event_type": event_type, "run_id": run_id})

    # After all retries exhausted (before _record_openclaw_delivery_failure)
    await self._trace_event("webhook.delivery.failed", {"event_type": event_type, "run_id": run_id, **failure}, level="error")
```

- [ ] **Step 3: Update app.py to pass trace_emitter to Scheduler and WebhookNotifier**

```python
    webhooks = WebhookNotifier(
        db,
        openclaw_hooks=settings.openclaw.hooks if settings.openclaw.hooks.enabled else None,
        trace_emitter=trace_emitter,
    )
    # ...
    scheduler = Scheduler(db, hosts, jobs, executor, webhooks, settings, state_machine=sm,
                          trace_emitter=trace_emitter)
```

- [ ] **Step 4: Run all tests**

Run: `python -m pytest tests/ -v`
Expected: ALL PASS

- [ ] **Step 5: Commit**

```bash
git add src/scheduler.py src/webhook_notifier.py src/app.py
git commit -m "feat(tracing): instrument scheduler and webhook_notifier + add event cleanup loop"
```

---

## Task 11: Final integration verification

- [ ] **Step 1: Run full test suite**

Run: `python -m pytest tests/ -v --tb=short`
Expected: ALL PASS

- [ ] **Step 2: Verify schema migration works on fresh DB**

```bash
rm -f .coop/state.db
python -c "
import asyncio
from src.database import Database
async def main():
    db = Database('.coop/state.db', 'db/schema.sql')
    await db.connect()
    row = await db.fetchone(\"SELECT sql FROM sqlite_master WHERE type='table' AND name='events'\")
    print(row['sql'])
    assert 'trace_id' in row['sql']
    print('Schema OK')
    await db.close()
asyncio.run(main())
"
```
Expected: Schema printed with all trace columns, "Schema OK"

- [ ] **Step 3: Verify diagnostic APIs respond**

```bash
python -c "
import asyncio
from src.app import app
from fastapi.testclient import TestClient
client = TestClient(app)
r = client.get('/health')
print('health:', r.json())
print('PASS' if r.status_code == 200 else 'FAIL')
"
```

- [ ] **Step 4: Commit**

```bash
git add -A
git commit -m "feat(tracing): complete test environment observability implementation"
```
