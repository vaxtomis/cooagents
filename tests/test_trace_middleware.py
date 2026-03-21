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
