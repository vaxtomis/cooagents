import asyncio
import pytest
from src.trace_context import new_trace, bind_run, bind_job, get_context


async def test_new_trace_generates_id():
    new_trace()
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
