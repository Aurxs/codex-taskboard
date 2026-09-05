"""In-process fan-out for the local SSE endpoint."""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from typing import Any


class EventBus:
    def __init__(self) -> None:
        self._subscribers: set[asyncio.Queue[dict[str, Any]]] = set()
        self._lock = asyncio.Lock()

    async def subscribe(self) -> asyncio.Queue[dict[str, Any]]:
        queue: asyncio.Queue[dict[str, Any]] = asyncio.Queue(maxsize=256)
        async with self._lock:
            self._subscribers.add(queue)
        return queue

    async def unsubscribe(self, queue: asyncio.Queue[dict[str, Any]]) -> None:
        async with self._lock:
            self._subscribers.discard(queue)

    async def publish(
        self,
        event_type: str,
        *,
        project_id: str | None = None,
        task_id: str | None = None,
        payload: Any = None,
    ) -> None:
        event: dict[str, Any] = {"type": event_type}
        if project_id is not None:
            event["projectId"] = project_id
        if task_id is not None:
            event["taskId"] = task_id
        event["payload"] = payload
        async with self._lock:
            queues = tuple(self._subscribers)
        for queue in queues:
            try:
                queue.put_nowait(event)
            except asyncio.QueueFull:
                # A stale UI must not back-pressure scheduling.  Drop its
                # oldest event and retain the newest snapshot.
                try:
                    queue.get_nowait()
                    queue.put_nowait(event)
                except asyncio.QueueEmpty:
                    pass

    async def stream(self) -> AsyncIterator[str]:
        queue = await self.subscribe()
        try:
            yield ": codex-taskboard connected\n\n"
            while True:
                event = await queue.get()
                yield f"data: {json.dumps(event, ensure_ascii=False, separators=(',', ':'))}\n\n"
        finally:
            await self.unsubscribe(queue)
