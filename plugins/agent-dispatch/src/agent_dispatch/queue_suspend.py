"""``TaskQueue`` mixin: suspend/resume and the cooldown monitor reconciler.

Extracted from :mod:`agent_dispatch.queue_lifecycle` (module-size discipline
-- see ``AGENTS.md``'s note on new additions going into a sibling mixin
rather than a near-ceiling module) as its own coherent responsibility band:
parking a task dormant, waking it back up, and -- Phase 3 of
``efforts/active/agent-dispatch-monitor-and-confirmed-state/README.md`` --
the periodic pass that auto-resumes a suspended task whose default
**cooldown monitor** (see :mod:`agent_dispatch.monitors`) has elapsed.

This mixin is composed into :class:`agent_dispatch.queue.TaskQueue` via
multiple inheritance; it relies on queue-owned primitives such as
``self._connect()``, ``self._fetch()``, ``self._audit()``, ``self._now()``,
``self._transition()``, and ``self._notify_owned_transition()`` (all defined
by sibling mixins) and is not usable standalone.

**Why a bare cooldown-due resume can reuse :meth:`resume` unchanged:**
``resume()`` already branches, inside ``_transition``'s
``reembody_headless_on_wake`` handling, between an interactive owner (a live
``owner_session_id`` -- transitions straight to ``started`` and optionally
enqueues a wake nudge) and a cold-headless owner (no live session -- stays
``suspended`` but flips ``resume_requested`` for the supervisor's own
``release_resumed_cold_tasks`` poll to pick up). The reconciler below never
needs to re-derive that split itself; it just calls ``resume()`` on the
task's own current owner once the monitor is due.
"""

from __future__ import annotations

from .monitors import DEFAULT_SUSPEND_COOLDOWN_SECONDS, MonitorKind, suspend_monitor_columns
from .queue_common import (
    PROGRESS_SUMMARY_MAX,
    Task,
    _check_expected_status,
    _clip,
    _task_transition_spec,
)
from .queue_records import Status, TaskError


class QueueSuspendMixin:
    """Suspend, resume, and cooldown-monitor reconciliation for :class:`TaskQueue`."""

    def suspend(
        self,
        task_id: str,
        worker_id: str,
        *,
        reason: str,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        expected_owner_session_id: str | None = None,
        reject_pending_steer: bool = True,
        cooldown_seconds: float | None = DEFAULT_SUSPEND_COOLDOWN_SECONDS,
        now: float | None = None,
    ) -> Task:
        """Park a ``started`` task as dormant while preserving its owner.

        Per the vision's *suspension-requires-a-monitor* behavior, a bare
        suspend (``cooldown_seconds`` left at its default) always gets a
        real, bounded, checkable monitor -- never an unwatched idle. Pass
        ``cooldown_seconds=None`` only when the caller has arranged its own,
        more specific wait (e.g. an operator steer answer already pending).
        """
        meaningful = _clip(reason, PROGRESS_SUMMARY_MAX)
        if meaningful is None:
            raise TaskError("suspend requires a non-empty reason")
        ts = self._now(now)
        monitor_columns = suspend_monitor_columns(now=ts, cooldown_seconds=cooldown_seconds)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            current = self._fetch(conn, task_id)
            if current is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such task {task_id!r}")
            try:
                _check_expected_status(current, expected_status)
            except TaskError:
                conn.execute("COMMIT")
                raise
            if current.status == Status.SUSPENDED and current.owner == worker_id:
                conn.execute(
                    "UPDATE tasks SET monitor_kind = ?, monitor_not_before = ?,"
                    " updated_at = ? WHERE id = ?",
                    (
                        monitor_columns["monitor_kind"],
                        monitor_columns["monitor_not_before"],
                        ts,
                        task_id,
                    ),
                )
                self._audit(
                    conn,
                    task_id,
                    ts=ts,
                    from_status=Status.SUSPENDED,
                    to_status=Status.SUSPENDED,
                    worker=worker_id,
                    note=f"suspend: {meaningful}",
                )
                result = self._fetch(conn, task_id)
                conn.execute("COMMIT")
                return result  # type: ignore[return-value]
            conn.execute("COMMIT")
        allowed, to = _task_transition_spec("suspend")
        return self._transition(
            task_id,
            allowed=allowed,
            to=to,
            worker_id=worker_id,
            now=ts,
            note=f"suspend: {meaningful}",
            extra={"lease_expires_at": None, "last_liveness": None, **monitor_columns},
            expected_generation=expected_generation,
            expected_owner_session_id=expected_owner_session_id,
            reject_pending_steer=reject_pending_steer,
        )

    def resume(
        self,
        task_id: str,
        worker_id: str,
        *,
        wake_requested: bool = False,
        wake_message: str | None = None,
        adopt_owner_session_id: str | None = None,
        reuse_session: bool = False,
        expected_owner_session_id: str | None = None,
        expected_generation: int | None = None,
        now: float | None = None,
    ) -> Task:
        """Wake an owned ``suspended`` task back to ``started``."""
        ts = self._now(now)
        extra: dict[str, object] = {
            "lease_expires_at": ts + self.lease_seconds,
            "last_seen_at": ts,
            "last_liveness": None,
            "resume_requested": 0,
            "monitor_kind": None,
            "monitor_not_before": None,
        }
        if adopt_owner_session_id is not None:
            extra["owner_session_id"] = adopt_owner_session_id
        allowed, to = _task_transition_spec("resume")
        result = self._transition(
            task_id,
            allowed=allowed,
            to=to,
            worker_id=worker_id,
            now=ts,
            note="resume",
            extra=extra,
            bump_generation=adopt_owner_session_id is not None,
            expected_owner_session_id=expected_owner_session_id,
            expected_generation=expected_generation,
            reembody_headless_on_wake=not reuse_session,
            wake_requested=wake_requested,
            wake_message=wake_message,
            reject_if_held=True,
            idempotent_replay=adopt_owner_session_id is None,
        )
        self._notify_owned_transition()
        return result

    def reconcile_cooldowns(self, *, now: float | None = None) -> int:
        """Auto-resume every ``suspended`` task whose cooldown has elapsed.

        A companion reconciliation pass to the liveness GC / orphan reap /
        handoff-fallback loops (see :mod:`agent_dispatch.coordinator_loops`):
        this is what makes a bare suspend a genuine round-robin time-slice
        rather than a wait nothing ever revisits. Reuses :meth:`resume`
        unchanged for both an interactive and a cold-headless owner -- see
        this module's docstring. A task that raced with a manual
        resume/steer/abandon in between is simply skipped this pass.
        """
        ts = self._now(now)
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT id, owner FROM tasks WHERE status = ? AND monitor_kind = ?"
                " AND monitor_not_before IS NOT NULL AND monitor_not_before <= ?",
                (Status.SUSPENDED, MonitorKind.COOLDOWN.value, ts),
            ).fetchall()
        resumed = 0
        for row in rows:
            task_id, owner = row["id"], row["owner"]
            if not owner:
                continue
            try:
                self.resume(
                    task_id,
                    owner,
                    wake_requested=True,
                    wake_message="cooldown elapsed; resuming automatically",
                    now=ts,
                )
            except TaskError:
                continue
            resumed += 1
        return resumed
