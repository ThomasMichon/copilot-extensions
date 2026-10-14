"""Task-scoped authorization helpers for coordinator task routes.

Split out of :mod:`agent_dispatch.coordinator_tasks` purely to keep that
module under this repo's module-size cap -- see the ``agent-dispatch-fleet-
authority`` effort, Phase 1 (issue #6617), for the design rationale (the
caller-asserted-``worker_id``-is-unbound gap this closes, and why the
fencing-epoch check belongs here).
"""

from __future__ import annotations

from fastapi import HTTPException, Request

from .lease import FencingGuard
from .queue import TaskQueue
from .task_scope import mint_task_scope_token


def resolve_task_authority(
    request: Request,
    queue: TaskQueue,
    fencing_guard: FencingGuard | None,
    task_id: str,
    worker_id: str | None,
    owner_session_id: str | None = None,
) -> tuple[str | None, str | None]:
    """The effective ``(worker_id, owner_session_id)`` for a task-lifecycle
    mutation on ``task_id``.

    A caller authenticated with the full shared/control bearer is trusted as
    today: the body-asserted values pass through unchanged. A caller
    authenticated with a task-scoped token (see
    ``coordinator_auth._make_auth`` and ``.task_scope``) instead has its
    claims re-checked against the task's CURRENT state -- this is the actual
    authorization boundary a projected relay needs: the token alone proves
    nothing by signature validity alone; it must still match who currently
    owns the task, and the coordinator epoch it was minted under must not
    have been superseded. On any mismatch this raises 403 rather than
    silently falling back to the body's (unauthenticated-for-this-purpose)
    assertion.
    """
    scope = getattr(request.state, "task_scope", None)
    if scope is None:
        return worker_id, owner_session_id
    if scope.task_id != task_id:
        raise HTTPException(
            status_code=403,
            detail="task-scoped token is not valid for this task",
        )
    task = queue.get(task_id)
    if task is None:
        raise HTTPException(status_code=404, detail="no such task")
    if task.owner != scope.worker_id or task.owner_session_id != scope.owner_session_id:
        raise HTTPException(
            status_code=403,
            detail="task-scoped token no longer matches the task's current state",
        )
    if fencing_guard is not None and not fencing_guard.is_valid(scope.epoch):
        raise HTTPException(
            status_code=403,
            detail="task-scoped token's coordinator epoch has been superseded",
        )
    return scope.worker_id, scope.owner_session_id


def mint_task_token(
    control_token: str | None,
    fencing_guard: FencingGuard | None,
    task_id: str,
    worker_id: str,
    owner_session_id: str | None,
) -> str | None:
    """Mint a task-scoped token for a just-established ownership, or
    ``None`` when no ``control_token`` is configured (task-scoped tokens are
    keyed off it, mirroring ``coordinator_auth.scoped_control_token``) or the
    task has no ``owner_session_id`` yet to bind."""
    if control_token is None or owner_session_id is None:
        return None
    epoch = fencing_guard.current_epoch() if fencing_guard is not None else 0
    return mint_task_scope_token(
        control_token,
        task_id=task_id,
        worker_id=worker_id,
        owner_session_id=owner_session_id,
        epoch=epoch,
    )
