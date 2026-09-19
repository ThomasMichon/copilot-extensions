"""Phase 2: ``force-stop`` -- terminate the exact current live session for a
``started`` task and park it as ``suspended``, without implying or requiring
an operator hold (:meth:`agent_dispatch.queue.TaskQueue.set_hold` is a
separate, durable primitive -- see that method's own docstring). This is the
"stop it now" verb: an operator-initiated hard termination, distinct from a
pause (which never itself ends a live session).

Session termination is a **best-effort** step: it is attempted when the task
carries a captured ``owner_session_id`` (item 3's interactive-embodiment
transaction, or a headless body's ``bind_owner_session``, both capture one),
but the state transition to ``suspended`` proceeds regardless of whether
termination could be confirmed -- the operator's actual intent (stop treating
this task as actively running) is honored even when the live-process
teardown itself could not be verified. A **local** session (same machine as
this call) is ended via :func:`agent_dispatch.bridge.force_end_session`
(``agent-bridge end <id> --force``); a **fleet** one (a different machine,
per the task's ``owner`` = ``machine/worktree``) is ended via
:func:`agent_dispatch.embody.stop_fleet_body` over the SSH mesh -- the same
generic, unconditional termination primitive :mod:`agent_dispatch.embody`
already uses for a fleet body's own cold/end paths, no new mechanism.

Only a ``started`` task is eligible: ``suspend`` (the transition force-stop
lands on) has no legal ``claimed`` origin in ``task_state_machine.py``, and a
merely-``claimed`` task never had a live session worth force-terminating in
the first place -- an operator wanting to give it up uses ``yield``/
``abandon`` instead.
"""

from __future__ import annotations

from typing import Any, Protocol

from .queue_records import Status


class ForceStopError(RuntimeError):
    """Raised when a task isn't eligible for force-stop."""


class _Client(Protocol):
    def get(self, task_id: str) -> dict: ...

    def suspend(
        self,
        task_id: str,
        worker_id: str,
        *,
        reason: str,
        expected_status: str | None = None,
        expected_generation: int | None = None,
        expected_owner_session_id: str | None = None,
    ) -> dict: ...


def force_stop(
    client: _Client,
    task_id: str,
    *,
    local_machine: str | None = None,
    actor: str | None = None,
) -> dict[str, Any]:
    """Terminate ``task_id``'s exact current session (best-effort) and park
    it ``suspended`` under its existing owner, fenced against the exact
    status/generation/owner-session read at the top of this call.

    Returns ``{"task_id", "session", "session_stopped", "task"}`` --
    ``session_stopped`` is ``None`` when there was no captured session to
    stop (nothing attempted), else the termination attempt's own bool
    result. Raises :class:`ForceStopError` when the task is not ``started``.
    """
    task = client.get(task_id)
    status = task.get("status")
    if status != Status.STARTED:
        raise ForceStopError(
            f"task {task_id!r} is {status!r}; force-stop requires started "
            "(a merely-claimed task never had a live session to stop -- use "
            "yield/abandon instead)"
        )
    owner = task.get("owner")
    session_id = task.get("owner_session_id")
    generation = task.get("generation")

    session_stopped: bool | None = None
    if session_id:
        machine = owner.split("/", 1)[0] if owner and "/" in owner else None
        if local_machine and machine and machine != local_machine:
            from . import embody

            session_stopped = embody.stop_fleet_body(machine, session_id)
        else:
            from . import bridge

            session_stopped = bridge.force_end_session(session_id)

    reason = f"force-stopped by {actor}" if actor else "force-stopped"
    bound = client.suspend(
        task_id,
        owner,
        reason=reason,
        expected_status=status,
        expected_generation=generation,
        expected_owner_session_id=session_id,
    )
    return {
        "task_id": task_id,
        "session": session_id,
        "session_stopped": session_stopped,
        "task": bound,
    }
