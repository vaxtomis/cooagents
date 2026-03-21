"""Async-safe correlation context propagation via contextvars."""
from __future__ import annotations

import uuid
from contextvars import ContextVar

_trace_id: ContextVar[str] = ContextVar("trace_id", default="")
_run_id: ContextVar[str | None] = ContextVar("run_id", default=None)
_job_id: ContextVar[str | None] = ContextVar("job_id", default=None)
_span_type: ContextVar[str] = ContextVar("span_type", default="request")


def new_trace(trace_id: str | None = None) -> str:
    """Generate (or accept) a trace_id and set it in context. Returns the trace_id string."""
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
