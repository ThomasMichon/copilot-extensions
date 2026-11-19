"""Original-deadline retirement and wake under the existing waiter transaction."""

from __future__ import annotations

from collections.abc import Callable
import sqlite3
from typing import Any, TYPE_CHECKING

from .queue_common import RunWaiterWakeOperation, Task
from .queue_feed_attempts import require_feed_expiry
from .queue_records import TaskError

EXPIRY_PROTOCOL = "agent-dispatch.repository-feed-expiry/v1"
_REASON = "original repository-feed deadline elapsed"
_SENDER = "agent-dispatch-feed-expiry"


class FeedWaiterExpiryMixin:
    repository_feed_expiry_protocol = EXPIRY_PROTOCOL

    if TYPE_CHECKING:
        _connect: Callable[[], sqlite3.Connection]
        _now: Callable[[float | None], float]
        _run_waiter_from_row: Callable[[sqlite3.Row], dict[str, Any]]
        _fetch: Callable[[sqlite3.Connection, str], Task | None]
        _enqueue_run_waiter_wake: Callable[..., RunWaiterWakeOperation]
        _audit: Callable[..., None]
        _notify_wake: Callable[[], None]

    def expire_repository_feed_waiter(
        self, waiter: dict[str, Any], *, now: float | None = None,
    ) -> dict[str, Any] | None:
        """Retire and queue one native timeout; never invent a service event or ACK."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute("SELECT * FROM run_waiters WHERE id=?", (waiter["id"],)).fetchone()
            if row is None:
                raise TaskError("original repository-feed waiter unavailable")
            current = self._run_waiter_from_row(row)
            observed = waiter.get("delegation")
            retained = current.get("delegation")
            if (
                not isinstance(observed, dict) or not isinstance(retained, dict)
                or "feed" not in retained
                or {key: value for key, value in observed.items() if key != "registration_id"}
                != {key: value for key, value in retained.items() if key != "registration_id"}
                or any(current[key] != waiter.get(key) for key in (
                    "task_id", "generation", "task_generation", "owner",
                    "owner_session_id", "feed_admission",
                ))
            ):
                raise TaskError("original repository-feed expiry identity differs")
            if current["state"] == "retired" and current["retired_reason"] == _REASON:
                wake = conn.execute(
                    "SELECT id FROM run_waiter_wakes WHERE task_id=? AND waiter_generation=?"
                    " AND task_generation=? AND owner=? AND owner_session_id=? AND sender=?",
                    (current["task_id"], current["generation"], current["task_generation"],
                     current["owner"], current["owner_session_id"], _SENDER),
                ).fetchone()
                if wake is None:
                    raise TaskError("expired repository-feed waiter has no original native wake")
                conn.execute("COMMIT")
                return {**current, "wake_id": wake["id"]}
            if current["state"] not in ("preparing", "active"):
                conn.execute("COMMIT")
                return None
            task = self._fetch(conn, current["task_id"])
            require_feed_expiry(conn, task, current, now=ts)
            conn.execute(
                "UPDATE run_waiters SET state='retired', retired_reason=?, updated_at=? WHERE id=?",
                (_REASON, ts, current["id"]),
            )
            wake_operation = self._enqueue_run_waiter_wake(
                conn, current, message="PR feed wait timed out; re-check external state.",
                sender=_SENDER, ts=ts,
            )
            assert task is not None
            self._audit(
                conn, task.id, ts=ts, from_status=task.status, to_status=task.status,
                worker=task.owner, note="repository-feed waiter expired at original deadline",
            )
            result = conn.execute("SELECT * FROM run_waiters WHERE id=?", (current["id"],)).fetchone()
            conn.execute("COMMIT")
        self._notify_wake()
        return {**self._run_waiter_from_row(result), "wake_id": wake_operation.id}
