"""SSE routes for live run event streaming."""
from __future__ import annotations

import asyncio
import json

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import StreamingResponse


def create_sse_router(db=None, broadcaster=None):
    router = APIRouter(tags=["events"])

    def _get_db(request: Request | None = None):
        if db is not None:
            return db
        return request.app.state.db

    def _get_broadcaster(request: Request | None = None):
        if broadcaster is not None:
            return broadcaster
        return request.app.state.sse_broadcaster

    @router.get("/runs/{run_id}/events/stream")
    async def run_events_stream(run_id: str, request: Request):
        d = _get_db(request)
        run = await d.fetchone("SELECT id FROM runs WHERE id=?", (run_id,)) if d else None
        if d and not run:
            raise HTTPException(status_code=404, detail=f"Run {run_id} not found")

        stream_broadcaster = _get_broadcaster(request)
        queue = stream_broadcaster.subscribe(run_id)

        async def event_stream():
            try:
                yield ": connected\n\n"
                while True:
                    try:
                        message = await asyncio.wait_for(queue.get(), timeout=1.0)
                    except asyncio.TimeoutError:
                        if await request.is_disconnected():
                            break
                        continue

                    yield f"event: {message['event']}\n"
                    yield f"data: {json.dumps(message['data'], ensure_ascii=False)}\n\n"

                    if await request.is_disconnected():
                        break
            finally:
                stream_broadcaster.unsubscribe(run_id, queue)

        return StreamingResponse(event_stream(), media_type="text/event-stream")

    return router