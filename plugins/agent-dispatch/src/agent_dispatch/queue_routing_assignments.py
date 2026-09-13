"""``TaskQueue`` mixin: routing-assignment lifecycle and billing-ref tracking.

Extracted from :mod:`agent_dispatch.queue` (Phase 10 componentization,
``efforts/active/review-automation-reliability`` #2423) as the next
natural table cluster after ``queue_schedule_registry.py``: routing
provenance (which model/surface a spawn attempt was routed to, its
admit/launch/run/terminal lifecycle, and opaque provider billing-event
references) is a distinct concern from the spawn-reservation lifecycle
methods that surround it in ``queue.py``, and none of these six methods is
monkeypatched by name anywhere in the test suite -- so, like
``ScheduleRegistrationMixin``, this is a plain mixin.

Unlike ``queue_schedule_registry.py``'s original circular-import problem,
this module needs nothing back from ``queue.py`` at all:
``RoutingAssignment``/``normalize_assignment``/``RoutingProvenanceError``/
``routing_token``/``ROUTING_SCHEMA_VERSION``/``ACTOR_ROLES``/
``TERMINAL_DISPOSITIONS`` already live in :mod:`agent_dispatch.routing_provenance`
(a module ``queue.py`` itself only re-exports), and ``TaskError``/
``SpawnState`` live in the dependency-free :mod:`agent_dispatch.queue_records`
alongside the other shared record types. Every name below is an ordinary
top-level import with no cycle and no runtime-introspection gap.

:class:`RoutingAssignmentMixin` is composed into
:class:`agent_dispatch.queue.TaskQueue` via multiple inheritance; it relies
on ``self._connect()`` and ``self._now()`` from that class and is not
usable standalone.
"""

from __future__ import annotations

import sqlite3
from collections.abc import Mapping

from .queue_records import SpawnState, TaskError
from .routing_provenance import (
    ACTOR_ROLES,
    ROUTING_SCHEMA_VERSION,
    TERMINAL_DISPOSITIONS,
    RoutingAssignment,
    RoutingProvenanceError,
    normalize_assignment,
)
from .routing_provenance import (
    token as routing_token,
)


class RoutingAssignmentMixin:
    """Routing-assignment lifecycle and billing-ref methods for
    :class:`TaskQueue`."""

    def record_routing_assignment(
        self,
        reservation_key: str,
        assignment: Mapping[str, object],
        *,
        now: float | None = None,
    ) -> tuple[RoutingAssignment, bool]:
        """Attach one immutable routing decision to a spawn reservation.

        The reservation key is the assignment identity. Repeating the same
        payload is idempotent; changing immutable routing facts is rejected.
        """
        try:
            normalized = normalize_assignment(assignment)
        except RoutingProvenanceError as exc:
            raise TaskError(str(exc)) from exc
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            reservation = conn.execute(
                "SELECT task_id, attempt, state FROM spawn_reservations WHERE key = ?",
                (reservation_key,),
            ).fetchone()
            if reservation is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such reservation: {reservation_key}")
            existing = conn.execute(
                "SELECT * FROM routing_assignments WHERE assignment_id = ?",
                (reservation_key,),
            ).fetchone()
            immutable = {
                "task_id": reservation["task_id"],
                "attempt": reservation["attempt"],
                **normalized,
            }
            if existing is not None:
                current = RoutingAssignment.from_row(existing)
                current_values = {key: getattr(current, key) for key in immutable}
                conn.execute("COMMIT")
                if current_values != immutable:
                    raise TaskError("routing assignment already exists with different facts")
                return current, False
            if reservation["state"] != SpawnState.RESERVING:
                conn.execute("COMMIT")
                raise TaskError("new routing assignment requires a reserving spawn reservation")
            parent = normalized["parent_assignment_id"]
            if parent is not None:
                if parent == reservation_key:
                    conn.execute("COMMIT")
                    raise TaskError("routing assignment cannot parent itself")
                parent_row = conn.execute(
                    "SELECT 1 FROM routing_assignments WHERE assignment_id = ?",
                    (parent,),
                ).fetchone()
                if parent_row is None:
                    conn.execute("COMMIT")
                    raise TaskError(f"no such parent routing assignment: {parent}")
            conn.execute(
                "INSERT INTO routing_assignments ("
                "schema_version, assignment_id, task_id, attempt, "
                "parent_assignment_id, purpose, selected_model, "
                "eligibility_state, selection_reason, execution_surface, "
                "containment_profile_ref, trial_ref, decision_ref, "
                "coordinator_session_ref, state, created_at, updated_at"
                ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    ROUTING_SCHEMA_VERSION,
                    reservation_key,
                    reservation["task_id"],
                    reservation["attempt"],
                    normalized["parent_assignment_id"],
                    normalized["purpose"],
                    normalized["selected_model"],
                    normalized["eligibility_state"],
                    normalized["selection_reason"],
                    normalized["execution_surface"],
                    normalized["containment_profile_ref"],
                    normalized["trial_ref"],
                    normalized["decision_ref"],
                    normalized["coordinator_session_ref"],
                    "assigned",
                    ts,
                    ts,
                ),
            )
            conn.execute(
                "INSERT INTO routing_assignment_events ("
                "event_id, assignment_id, event_type, occurred_at, "
                "from_state, to_state, actor_role"
                ") VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    f"lifecycle:{reservation_key}:assigned",
                    reservation_key,
                    "assigned",
                    ts,
                    None,
                    "assigned",
                    "coordinator",
                ),
            )
            row = conn.execute(
                "SELECT * FROM routing_assignments WHERE assignment_id = ?",
                (reservation_key,),
            ).fetchone()
            conn.execute("COMMIT")
        return RoutingAssignment.from_row(row), True

    def transition_routing_assignment(
        self,
        assignment_id: str,
        event_type: str,
        actor_role: str,
        *,
        terminal_disposition: str | None = None,
        reason_code: str | None = None,
        worker_session_ref: str | None = None,
        now: float | None = None,
    ) -> tuple[RoutingAssignment, bool]:
        """Advance one routing assignment with atomic terminal semantics."""
        try:
            event_type = str(routing_token(event_type, field="event_type", limit=32))
            actor_role = str(routing_token(actor_role, field="actor_role", limit=32))
            reason_code = routing_token(
                reason_code,
                field="reason_code",
                limit=128,
                optional=True,
            )
            worker_session_ref = routing_token(
                worker_session_ref,
                field="worker_session_ref",
                optional=True,
            )
            disposition = routing_token(
                terminal_disposition,
                field="terminal_disposition",
                limit=32,
                optional=True,
            )
        except RoutingProvenanceError as exc:
            raise TaskError(str(exc)) from exc
        if actor_role not in ACTOR_ROLES:
            raise TaskError("actor_role is not supported")
        if event_type not in {"admitted", "denied", "launched", "running", "terminal"}:
            raise TaskError("routing event_type is not supported")
        if event_type == "denied":
            disposition = "denied"
        elif event_type == "terminal":
            if disposition not in TERMINAL_DISPOSITIONS - {"denied"}:
                raise TaskError("terminal disposition is not supported")
        elif disposition is not None:
            raise TaskError("terminal_disposition applies only to terminal events")
        target_state = "terminal" if event_type in {"denied", "terminal"} else event_type
        allowed = {
            "assigned": {"admitted", "terminal"},
            "admitted": {"launched", "terminal"},
            "launched": {"running", "terminal"},
            "running": {"terminal"},
            "terminal": set(),
        }
        ts = self._now(now)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            row = conn.execute(
                "SELECT * FROM routing_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if row is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such routing assignment: {assignment_id}")
            current = RoutingAssignment.from_row(row)
            if current.state == target_state:
                same_terminal = (
                    target_state == "terminal"
                    and current.terminal_disposition == disposition
                    and current.reason_code == reason_code
                )
                same_nonterminal = target_state != "terminal"
                conn.execute("COMMIT")
                if same_terminal or same_nonterminal:
                    return current, False
                raise TaskError("routing assignment has a conflicting terminal outcome")
            if event_type == "denied" and current.state != "assigned":
                conn.execute("COMMIT")
                raise TaskError("routing admission can be denied only before admission")
            if target_state not in allowed.get(current.state, set()):
                conn.execute("COMMIT")
                raise TaskError(
                    f"cannot transition routing assignment from "
                    f"{current.state!r} to {target_state!r}"
                )
            conn.execute(
                "UPDATE routing_assignments SET state = ?, "
                "terminal_disposition = ?, reason_code = ?, "
                "worker_session_ref = COALESCE(?, worker_session_ref), "
                "updated_at = ? WHERE assignment_id = ?",
                (
                    target_state,
                    disposition,
                    reason_code,
                    worker_session_ref,
                    ts,
                    assignment_id,
                ),
            )
            try:
                conn.execute(
                    "INSERT INTO routing_assignment_events ("
                    "event_id, assignment_id, event_type, occurred_at, "
                    "from_state, to_state, actor_role, reason_code, "
                    "worker_session_ref"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"lifecycle:{assignment_id}:{event_type}",
                        assignment_id,
                        event_type,
                        ts,
                        current.state,
                        target_state,
                        actor_role,
                        reason_code,
                        worker_session_ref,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise TaskError("routing lifecycle event conflicts with history") from exc
            row = conn.execute(
                "SELECT * FROM routing_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            conn.execute("COMMIT")
        return RoutingAssignment.from_row(row), True

    def record_routing_billing_ref(
        self,
        assignment_id: str,
        event_id: str,
        provider: str,
        provider_billing_event_ref: str,
        actor_role: str,
        *,
        occurred_at: float | None = None,
    ) -> bool:
        """Attach one opaque provider event reference exactly once."""
        try:
            event_id = str(routing_token(event_id, field="event_id"))
            provider = str(routing_token(provider, field="provider", limit=64))
            billing_ref = str(
                routing_token(
                    provider_billing_event_ref,
                    field="provider_billing_event_ref",
                )
            )
            actor_role = str(routing_token(actor_role, field="actor_role", limit=32))
        except RoutingProvenanceError as exc:
            raise TaskError(str(exc)) from exc
        if actor_role not in ACTOR_ROLES:
            raise TaskError("actor_role is not supported")
        ts = self._now(occurred_at)
        with self._connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            assignment = conn.execute(
                "SELECT state FROM routing_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
            if assignment is None:
                conn.execute("COMMIT")
                raise TaskError(f"no such routing assignment: {assignment_id}")
            existing = conn.execute(
                "SELECT * FROM routing_assignment_events WHERE event_id = ?",
                (f"billing:{event_id}",),
            ).fetchone()
            if existing is not None:
                identical = (
                    existing["assignment_id"] == assignment_id
                    and existing["actor_role"] == actor_role
                    and existing["provider"] == provider
                    and existing["provider_billing_event_ref"] == billing_ref
                )
                conn.execute("COMMIT")
                if identical:
                    return False
                raise TaskError("routing event_id already records different facts")
            try:
                conn.execute(
                    "INSERT INTO routing_assignment_events ("
                    "event_id, assignment_id, event_type, occurred_at, "
                    "from_state, to_state, actor_role, provider, "
                    "provider_billing_event_ref"
                    ") VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                    (
                        f"billing:{event_id}",
                        assignment_id,
                        "billing-linked",
                        ts,
                        assignment["state"],
                        assignment["state"],
                        actor_role,
                        provider,
                        billing_ref,
                    ),
                )
            except sqlite3.IntegrityError as exc:
                conn.execute("ROLLBACK")
                raise TaskError("provider billing event reference is already assigned") from exc
            conn.execute("COMMIT")
        return True

    def get_routing_assignment(self, assignment_id: str) -> RoutingAssignment | None:
        with self._connect() as conn:
            row = conn.execute(
                "SELECT * FROM routing_assignments WHERE assignment_id = ?",
                (assignment_id,),
            ).fetchone()
        return RoutingAssignment.from_row(row) if row is not None else None

    def list_routing_assignments(
        self,
        *,
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[RoutingAssignment]:
        limit = max(1, min(int(limit), 500))
        with self._connect() as conn:
            if task_id is None:
                rows = conn.execute(
                    "SELECT * FROM routing_assignments ORDER BY created_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT * FROM routing_assignments WHERE task_id = ? "
                    "ORDER BY attempt DESC LIMIT ?",
                    (task_id, limit),
                ).fetchall()
        return [RoutingAssignment.from_row(row) for row in rows]

    def routing_assignment_events(self, assignment_id: str) -> list[dict[str, object]]:
        if self.get_routing_assignment(assignment_id) is None:
            raise TaskError(f"no such routing assignment: {assignment_id}")
        with self._connect() as conn:
            rows = conn.execute(
                "SELECT event_id, assignment_id, event_type, occurred_at, "
                "from_state, to_state, actor_role, reason_code, "
                "worker_session_ref, provider, provider_billing_event_ref "
                "FROM routing_assignment_events WHERE assignment_id = ? "
                "ORDER BY id",
                (assignment_id,),
            ).fetchall()
        return [dict(row) for row in rows]
