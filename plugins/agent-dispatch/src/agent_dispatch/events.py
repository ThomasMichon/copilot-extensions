"""In-process event bus for coordinator SSE fan-out.

Coordinator mutations run in synchronous route handlers (worker threads), while
SSE subscribers live on the event loop. :meth:`EventBus.publish` is therefore
thread-safe: it hops back onto the bound loop before touching subscriber queues.
A slow subscriber drops events rather than back-pressuring producers.
"""

from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator

DEFAULT_MAX_QUEUE = 1000


class EventBus:
    """Fan-out of coordinator task events to any number of SSE subscribers."""

    def __init__(self, *, max_queue: int = DEFAULT_MAX_QUEUE):
        self._subscribers: set[asyncio.Queue[dict]] = set()
        self._loop: asyncio.AbstractEventLoop | None = None
        self._max_queue = max_queue

    def bind_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Record the running loop (called from the app lifespan startup)."""
        self._loop = loop

    def publish(self, event: dict) -> None:
        """Thread-safe publish: schedule fan-out on the bound loop.

        A no-op until the loop is bound (e.g. events raised before startup).
        """
        loop = self._loop
        if loop is None:
            return
        loop.call_soon_threadsafe(self._fanout, event)

    def _fanout(self, event: dict) -> None:
        for q in list(self._subscribers):
            try:
                q.put_nowait(event)
            except asyncio.QueueFull:
                pass  # slow subscriber: drop rather than block producers

    async def subscribe(self) -> AsyncIterator[dict]:
        """Yield events published after subscription, until the caller stops."""
        q: asyncio.Queue[dict] = asyncio.Queue(maxsize=self._max_queue)
        self._subscribers.add(q)
        try:
            while True:
                yield await q.get()
        finally:
            self._subscribers.discard(q)

    @property
    def subscriber_count(self) -> int:
        return len(self._subscribers)


def sse_format(event: dict) -> str:
    """Render an event as a Server-Sent-Events frame."""
    event_type = event.get("type", "message")
    return f"event: {event_type}\ndata: {json.dumps(event)}\n\n"
