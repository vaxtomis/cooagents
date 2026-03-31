"""Fire-and-forget trace event emitter with async queue consumer."""
from __future__ import annotations

import asyncio
import json
import logging
import traceback
from datetime import datetime, timezone

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

    def __init__(self, db=None, enabled: bool = True, broadcaster=None):
        self._db = db
        self._enabled = enabled
        self._broadcaster = broadcaster
        self._queue: asyncio.Queue = asyncio.Queue(maxsize=2048)
        self._running = False

    def set_db(self, db):
        """Wire the database after construction (avoids circular init)."""
        self._db = db

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
                logger.warning("trace consumer: batch write failed, %d events dropped", len(batch))

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
            run_id = ctx.get("run_id")
            trace_id = ctx.get("trace_id")
            job_id = ctx.get("job_id")
            span_type = ctx.get("span_type", "system")
            await self._db.execute(
                "INSERT INTO events (run_id, event_type, payload_json, created_at, "
                "trace_id, job_id, span_type, level, duration_ms, error_detail, source) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    run_id,
                    event_type,
                    json.dumps(payload) if payload is not None else None,
                    now,
                    trace_id,
                    job_id,
                    span_type,
                    level,
                    duration_ms,
                    error_detail,
                    source,
                ),
            )
            if self._broadcaster:
                await self._broadcaster.broadcast(
                    run_id,
                    event_type,
                    {
                        "run_id": run_id,
                        "event_type": event_type,
                        "payload": payload,
                        "created_at": now,
                        "trace_id": trace_id,
                        "job_id": job_id,
                        "span_type": span_type,
                        "level": level,
                        "duration_ms": duration_ms,
                        "error_detail": error_detail,
                        "source": source,
                    },
                )