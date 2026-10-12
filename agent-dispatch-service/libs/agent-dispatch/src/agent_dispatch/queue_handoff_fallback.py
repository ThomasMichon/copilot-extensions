"""``TaskQueue`` mixin: the atomic single-attempt claim for the coordinator's
handoff-fallback reconciliation (``efforts/active/context-handoff-overhaul``
Phase 3).

An unclaimed ``handoff``-labeled task sitting in ``proposed``/``queued`` past a
bounded window means nothing has picked up a stored continuation baton in
time. The coordinator's periodic reconciliation loop
(:mod:`agent_dispatch.coordinator`) detects this and, when enabled, launches a
fallback successor via agent-bridge's session-lifecycle ``--reclaim``
break-glass (:func:`agent_dispatch.bridge.spawn_worker`) -- agent-dispatch
judges *staleness*; agent-bridge performs the in-place replacement.

This module owns only the **idempotency guard**: a coordinator autonomously
spawning a real Copilot process must never double-launch, so the launch is
attempted **at most once per task** -- simpler than the ``spawn_reservations``
retry/attempt-budget machinery used for ordinary dispatch-queue work, since a
stale handoff either gets a single fallback attempt or is left for a human to
retry explicitly, never an automatic retry loop.

:class:`HandoffFallbackMixin` is composed into :class:`agent_dispatch.queue.TaskQueue`;
it relies on ``self._connect()`` and ``self._now()`` from that class and is not
usable standalone.
"""

from __future__ import annotations

import sqlite3


class HandoffFallbackMixin:
    """Handoff-fallback claim/outcome bookkeeping for :class:`TaskQueue`."""

    def _ensure_handoff_fallback_schema(self, conn: sqlite3.Connection) -> None:
        conn.execute(
            "CREATE TABLE IF NOT EXISTS handoff_fallback_attempts ("
            "  task_id TEXT PRIMARY KEY,"
            "  attempted_at REAL NOT NULL,"
            "  outcome TEXT NOT NULL DEFAULT 'pending',"
            "  detail TEXT,"
            "  updated_at REAL NOT NULL"
            ")"
        )

    def claim_handoff_fallback(self, task_id: str, *, now: float | None = None) -> bool:
        """Atomically claim the right to attempt one fallback launch for ``task_id``.

        Returns ``True`` the first time this is called for ``task_id`` (across
        every process sharing this database file -- the ``INSERT`` is the
        fence); every subsequent call for the same task returns ``False``
        without side effects, whether the prior attempt is still pending,
        succeeded, or failed. There is no expiry and no retry budget: at most
        one fallback launch is ever attempted per task by this mechanism.
        """
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            try:
                conn.execute(
                    "INSERT INTO handoff_fallback_attempts"
                    " (task_id, attempted_at, outcome, updated_at)"
                    " VALUES (?, ?, 'pending', ?)",
                    (task_id, ts, ts),
                )
            except sqlite3.IntegrityError:
                conn.execute("COMMIT")
                return False
            conn.execute("COMMIT")
        return True

    def record_handoff_fallback_outcome(
        self,
        task_id: str,
        *,
        outcome: str,
        detail: str | None = None,
        now: float | None = None,
    ) -> None:
        """Record the outcome of a claimed fallback attempt (observability only;
        the claim in :meth:`claim_handoff_fallback` already fences the launch
        itself, so a missing/failed outcome update never permits a retry)."""
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute(
                "UPDATE handoff_fallback_attempts"
                " SET outcome = ?, detail = ?, updated_at = ?"
                " WHERE task_id = ?",
                (outcome, detail, ts, task_id),
            )

    def get_handoff_fallback_attempt(self, task_id: str) -> dict | None:
        """Return the recorded attempt for ``task_id``, or ``None`` if never claimed."""
        with self._connect() as conn:
            row = conn.execute(
                "SELECT task_id, attempted_at, outcome, detail, updated_at"
                " FROM handoff_fallback_attempts WHERE task_id = ?",
                (task_id,),
            ).fetchone()
        if row is None:
            return None
        return dict(row)
