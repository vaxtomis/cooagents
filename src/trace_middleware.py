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
        # Resolve emitter lazily — may not be available at construction time
        emitter = self._emitter
        if emitter is None and hasattr(request.app.state, "trace_emitter"):
            emitter = request.app.state.trace_emitter

        # Read or generate trace_id
        trace_id = request.headers.get("x-trace-id") or None
        trace_id = new_trace(trace_id)

        start_time = time.monotonic()

        # Emit request.received
        if emitter:
            await emitter.emit(
                "request.received",
                {"method": request.method, "path": str(request.url.path)},
                source="middleware",
            )

        try:
            response = await call_next(request)
        except Exception as exc:
            duration_ms = int((time.monotonic() - start_time) * 1000)
            if emitter:
                await emitter.emit(
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
        if emitter:
            await emitter.emit(
                "request.completed",
                {"method": request.method, "path": str(request.url.path), "status": response.status_code},
                duration_ms=duration_ms,
                source="middleware",
            )

        return response
