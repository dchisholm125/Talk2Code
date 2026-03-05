from __future__ import annotations

import asyncio
from typing import Awaitable, Callable, Set

from core.events import ProgressUpdate

ProgressEventSink = Callable[[ProgressUpdate], Awaitable[None]]


class ObservabilityHub:
    def __init__(self) -> None:
        self._subscribers: Set[asyncio.Queue[ProgressUpdate]] = set()
        self._lock = asyncio.Lock()

    async def publish(self, update: ProgressUpdate) -> None:
        async with self._lock:
            queues = list(self._subscribers)
        for queue in queues:
            try:
                queue.put_nowait(update)
            except asyncio.QueueFull:
                # drop the oldest frame so we can make room
                try:
                    queue.get_nowait()
                except asyncio.QueueEmpty:
                    pass
                try:
                    queue.put_nowait(update)
                except asyncio.QueueFull:
                    continue

    def subscribe(self, maxsize: int = 64) -> asyncio.Queue[ProgressUpdate]:
        queue: asyncio.Queue[ProgressUpdate] = asyncio.Queue(maxsize=maxsize)
        self._subscribers.add(queue)
        return queue

    def unsubscribe(self, queue: asyncio.Queue[ProgressUpdate]) -> None:
        self._subscribers.discard(queue)


_hub = ObservabilityHub()


def get_observability_hub() -> ObservabilityHub:
    return _hub
