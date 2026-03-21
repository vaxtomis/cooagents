import asyncio
import pytest
from unittest.mock import AsyncMock
from src.trace_context import new_trace, bind_run
from src.trace_emitter import format_error, TraceEmitter


def test_format_error_basic():
    try:
        raise ValueError("test error")
    except ValueError as exc:
        result = format_error(exc)
    assert "ValueError" in result
    assert "test error" in result


def test_format_error_truncates():
    def deep_call(n):
        if n <= 0:
            raise ValueError("deep error")
        return deep_call(n - 1)

    try:
        deep_call(15)
    except ValueError as exc:
        result = format_error(exc, max_lines=8)
    lines = result.strip().splitlines()
    assert len(lines) <= 8
    assert "... truncated ..." in result


async def test_emitter_writes_to_db():
    db = AsyncMock()
    emitter = TraceEmitter(db=db, enabled=True)
    consumer_task = asyncio.create_task(emitter.start_consumer())

    new_trace("test-trace")
    bind_run("run-001")
    await emitter.emit("stage.transition", {"from": "INIT", "to": "REQ_COLLECTING"})
    await asyncio.sleep(0.2)

    emitter.stop()
    await consumer_task

    db.execute.assert_called()
    call_args = db.execute.call_args
    sql = call_args[0][0]
    assert "INSERT INTO events" in sql
    params = call_args[0][1]
    assert "test-trace" in params


async def test_emitter_never_raises():
    emitter = TraceEmitter(enabled=True)
    await emitter.emit("test.event", {"key": "val"})
    emitter.emit_sync("test.event", {"key": "val"})


async def test_emitter_disabled():
    db = AsyncMock()
    emitter = TraceEmitter(db=db, enabled=False)
    await emitter.emit("test.event")
    db.execute.assert_not_called()


def test_emit_sync_never_raises():
    emitter = TraceEmitter(enabled=True)
    emitter.emit_sync("test.event", {"key": "val"})
