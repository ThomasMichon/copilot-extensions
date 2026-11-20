"""Pending event writes indexed by session, including batches held by the writer."""

from __future__ import annotations

from collections import Counter
from collections.abc import Iterator
from contextlib import contextmanager
from queue import Queue
from threading import Condition

EventWrite = tuple[str, int, str, str, float]


class EventWriteQueue(Queue[object]):
    def __init__(self) -> None:
        super().__init__()
        self._changed = Condition()
        self._pending: Counter[str] = Counter()

    def put(self, item: object, block: bool = True, timeout: float | None = None) -> None:
        with self._changed:
            super().put(item, block, timeout)
            if isinstance(item, tuple):
                self._pending[item[0]] += 1

    def finish_batch(self, batch: list[EventWrite]) -> None:
        with self._changed:
            for item in batch:
                sid = item[0]
                self._pending[sid] -= 1
                if not self._pending[sid]:
                    del self._pending[sid]
                self.task_done()
            self._changed.notify_all()

    def wait_for_session(self, sid: str) -> None:
        with self._changed:
            self._changed.wait_for(lambda: sid not in self._pending)

    @contextmanager
    def if_settled(self, sid: str) -> Iterator[bool]:
        if not self._changed.acquire(blocking=False):
            yield False
            return
        try:
            yield sid not in self._pending
        finally:
            self._changed.release()
