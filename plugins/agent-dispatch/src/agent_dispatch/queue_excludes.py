"""``TaskQueue`` mixin: undoing a self-exclusion appended by ``yield_task``.

Extracted as its own small mixin (rather than folded into
:mod:`agent_dispatch.queue_lifecycle`, which is already at this repo's
1000-line module cap) because it is a narrow, self-contained undo for one
specific field: a worker's own ``yield --exclude``/``--exclude-self`` token.

This mixin is composed into :class:`agent_dispatch.queue.TaskQueue` via
multiple inheritance; it relies on queue-owned storage helpers such as
``self._connect()``, ``self._fetch()``, ``self._audit()``, and ``self._now()``
and is not usable standalone.
"""

from __future__ import annotations

import json

from .queue_common import Task, _check_expected_status
from .queue_records import TaskError


class QueueExcludeMixin:
    """Undo-a-self-exclusion method for :class:`TaskQueue`."""

    def clear_exclude(
        self,
        task_id: str,
        *,
        exclude: str | None = None,
        actor: str | None = None,
        expected_status: str | None = None,
        now: float | None = None,
    ) -> Task:
        """Remove a self-exclusion appended by :meth:`QueueLifecycleMixin.yield_task`'s
        ``exclude``/``--exclude-self`` -- the symmetric counterpart ``yield``
        never got. Without a durable way to undo it, a worker's own
        self-exclusion (e.g. ``machine:tmichon-cloud2`` when that machine is
        the lane's only permitted one) permanently strands the task: every
        future supervisor cycle treats it as ineligible forever, with no
        error, card, or dead-letter signal -- just silent, invisible
        starvation.

        ``exclude`` removes exactly that token; omitting it clears every
        exclusion on the task (mirrors ``clear_hold``'s "clear everything"
        shape, since a caller untangling a stuck task rarely wants to guess
        at the exact token spelling first).
        """
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            task = self._fetch(conn, task_id)
            if task is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            existing = list(task.excludes or [])
            if not existing:
                conn.execute("COMMIT")
                return task
            _check_expected_status(task, expected_status)
            if exclude is None:
                remaining: list[str] = []
            else:
                remaining = [item for item in existing if item != exclude]
            if remaining == existing:
                conn.execute("COMMIT")
                return task
            conn.execute(
                "UPDATE tasks SET excludes = ?, updated_at = ? WHERE id = ?",
                (json.dumps(remaining), ts, task_id),
            )
            cleared = [item for item in existing if item not in remaining]
            self._audit(
                conn,
                task_id,
                ts=ts,
                from_status=task.status,
                to_status=task.status,
                worker=actor,
                note=f"unexclude (cleared: {', '.join(cleared)})",
            )
            result = self._fetch(conn, task_id)
            conn.execute("COMMIT")
        return result  # type: ignore[return-value]
