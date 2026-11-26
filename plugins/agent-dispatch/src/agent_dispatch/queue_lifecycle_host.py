"""Queue-owned helpers the lifecycle mixin relies on, declared for type checking."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3
from typing import TYPE_CHECKING

from .queue_common import Task


class QueueLifecycleHost:
    if TYPE_CHECKING:
        lease_seconds: int
        _connect: Callable[[], sqlite3.Connection]
        _now: Callable[[float | None], float]
        _fetch: Callable[[sqlite3.Connection, str], Task | None]
        _audit: Callable[..., None]
        _encode_result: Callable[[object | None], str | None]
        _enqueue_wake: Callable[..., object]
        _has_headless_reservation: Callable[[sqlite3.Connection, str], bool]
        _notify_verification: Callable[[], None]
        _notify_wake: Callable[[], None]
        _record_attachment: Callable[..., None]
        get: Callable[[str], Task | None]

        @staticmethod
        def _insert_verification_request(
            conn: sqlite3.Connection, task_id: str, generation: int, trigger: str, ts: float,
            *, not_before: float | None = None,
        ) -> sqlite3.Row: ...


def single_task(value: Task | tuple[Task, bool]) -> Task:
    """Narrow a transition made without ``report_replay`` to its task."""
    assert not isinstance(value, tuple)
    return value
