"""Simple in-process broadcaster for run-scoped SSE streams."""
from __future__ import annotations

import asyncio


class SSEBroadcaster:
    def __init__(self) -> None:
        self._subscribers: dict[str, list[asyncio.Queue]] = {}

    def subscribe(self, run_id: str) -> asyncio.Queue:
        queue: asyncio.Queue = asyncio.Queue()
        self._subscribers.setdefault(run_id, []).append(queue)
        return queue

    def unsubscribe(self, run_id: str, queue: asyncio.Queue) -> None:
        queues = self._subscribers.get(run_id)
        if not queues:
            return
        self._subscribers[run_id] = [candidate for candidate in queues if candidate is not queue]
        if not self._subscribers[run_id]:
            self._subscribers.pop(run_id, None)

    async def broadcast(self, run_id: str | None, event_type: str, data: dict) -> None:
        if not run_id:
            return

        for queue in list(self._subscribers.get(run_id, [])):
            await queue.put({"event": event_type, "data": data})