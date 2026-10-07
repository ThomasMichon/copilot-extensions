"""Settle a ``spawned``/``cold`` spawn reservation whose OWN task has
already independently moved on to ``queued``+unowned, once its body is
freshly confirmed gone.

Extracted as its own coherent responsibility, mirroring
:mod:`agent_dispatch.spawn_cold_recovery`/:mod:`agent_dispatch
.spawn_releasing_recovery`'s own shape for the same module-size-discipline
reason (``AGENTS.md``). ``supervisor`` below is a
:class:`agent_dispatch.supervisor.Supervisor` instance; this module has no
dependency on that class beyond the attributes/methods it reads.
"""

from __future__ import annotations

import json
import logging
from typing import Any

from .client import DispatchError
from .queue import SpawnState, Status
from .spawn_factories import (
    _parse_fleet_body_handle,
    _parse_local_body_handle,
    _parse_script_body_handle,
    _tracking,
)
from .supervisor_conclusion import _CONCLUSION_COMPLETE

log = logging.getLogger("agent-dispatch.supervisor")


def _reservation_body_confirmed_gone(supervisor: Any, reservation: dict) -> bool:
    """``True`` only on a POSITIVE, exact-absence verdict for the body
    behind ``reservation``'s own ``session_handle`` -- the same
    fleet/local/script/worktree dispatch :mod:`agent_dispatch.supervisor`
    already uses elsewhere (e.g. ``Supervisor._reservation_has_live_process``),
    but answering the opposite question: this is a plain, direct "is it
    actually confirmed dead right now" probe, not that method's capacity-
    accounting-shaped "assume live unless proven otherwise AND the task
    happens to already be RELEASING/SUSPENDED" rule. Never returns ``True``
    on an exception or an ``UNKNOWN`` verdict -- ignorance must never be
    read as absence.
    """
    fleet = _parse_fleet_body_handle(reservation.get("session_handle"))
    if fleet is not None:
        try:
            return supervisor.fleet_verdict_fn(*fleet) == _tracking().GONE
        except Exception:
            return False
    local_sid = _parse_local_body_handle(reservation.get("session_handle"))
    if local_sid is not None:
        try:
            return supervisor.local_body_verdict_fn(local_sid) == _tracking().GONE
        except Exception:
            return False
    script_handle = _parse_script_body_handle(reservation.get("session_handle"))
    if script_handle is not None:
        _worker_id, pid, start_token, _task_file = script_handle
        try:
            return supervisor.script_body_verdict_fn(pid, start_token) == _tracking().GONE
        except Exception:
            return False
    return False


def reconcile_requeued_task_reservations(supervisor: Any) -> int:
    """Settle a `spawned`/`cold` reservation whose OWN task has already
    moved on to ``queued``+unowned, with its body confirmed gone.

    Closes a gap ``Supervisor.reconcile()`` deliberately does not: that
    method only ever settles a reservation once its task reaches a
    *terminal* status (SUBMITTED/COMPLETED/ABANDONED) -- by design, so a
    task merely requeued (e.g. by ``TaskQueue.reconcile_liveness``'s own
    owner-gone path) never gets its prior attempt's reservation
    auto-settled, since nothing else ever re-checks it either. Confirmed
    live (ThomasMichon/copilot-extensions#5563): a confirmed-dead headless
    review-dispatch body left its reservation `spawned` forever once its
    task requeued -- `reserve_spawn`'s own exclusive-key dedupe (and the
    plain active-reservation check for an unpinned task) then reads that
    dangling row as "an attempt is still in flight," permanently refusing a
    fresh spawn for a task the pool is otherwise completely free to retry.

    Deliberately re-probes liveness FRESH, right now, rather than trusting
    whatever verdict originally requeued the task -- the same exact-absence
    discipline ``reconcile()``'s own terminal-task branch already relies on
    (:func:`_reservation_body_confirmed_gone` mirrors that exact
    fleet/local/script dispatch). This is what keeps
    ``test_requeued_task_is_not_double_spawned``'s own invariant intact: a
    reservation whose body a caller's verdict function still reports LIVE
    (or UNKNOWN) is left untouched, exactly as before -- only a FRESH,
    POSITIVE gone confirmation, probed here, ever settles one.

    Returns the number of reservations settled this pass.
    """
    reconciled = 0
    for res in supervisor._list_reservations_safe(
        state=f"{SpawnState.SPAWNED},{SpawnState.COLD}",
    ):
        try:
            task = supervisor.client.get(res["task_id"])
        except DispatchError:
            continue  # task vanished; leave the reservation for a human
        if task.get("status") != Status.QUEUED or task.get("owner"):
            continue  # not yet released back to the pool -- still in play
        if not supervisor._matches_pool(task):
            continue
        if not _reservation_body_confirmed_gone(supervisor, res):
            continue  # live, or merely unknown -- never settle on ignorance
        detail = (
            "owner confirmed gone after the task requeued; releasing the "
            "stale spawn reservation so a fresh attempt can be reserved"
        )
        try:
            supervisor.client.request_spawn_release(
                res["key"], detail=detail, disposition="settled",
            )
            supervisor.client.retire_spawn(
                res["key"],
                exact_absence=True,
                detail=detail,
                conclusion_state=_CONCLUSION_COMPLETE,
                conclusion_detail=json.dumps(
                    {"action": "reconciled", "reason": "requeued-task-owner-gone"},
                    sort_keys=True,
                    separators=(",", ":"),
                ),
            )
        except DispatchError:
            log.exception(
                "could not reconcile stale spawn reservation %s for "
                "requeued task %s",
                res["key"],
                res["task_id"],
            )
            continue
        reconciled += 1
    return reconciled
