"""Task-scoped authorization helpers for coordinator task routes.

Split out of :mod:`agent_dispatch.coordinator_tasks` purely to keep that
module under this repo's module-size cap -- see the ``agent-dispatch-fleet-
authority`` effort, Phase 1 (issue #6617), for the design rationale (the
caller-asserted-``worker_id``-is-unbound gap this closes, and why the
fencing-epoch check belongs here).
"""

from __future__ import annotations

import time

from fastapi import Depends, FastAPI, HTTPException, Request, Response

from .coordinator_auth import _make_control_auth
from .coordinator_task_models import TaskGrantBody
from .events import EventBus
from .lease import FencingGuard
from .queue import Status, TaskQueue
from .task_scope import DEFAULT_TTL_SECONDS, mint_task_scope_token


def register_task_grant_route(
    app: FastAPI,
    queue: TaskQueue,
    control_token: str | None,
    fencing_guard: FencingGuard | None,
    bus: EventBus,
) -> None:
    control_auth = _make_control_auth(
        control_token, lambda kind, detail: bus.publish({"type": kind, **detail}),
        operation="task_grant", code_prefix="task_grant",
        rejection_event="task.grant_rejected",
        unavailable_message="task grants require configured control authority",
        forbidden_message="task grants require control authority",
    )

    @app.post("/tasks/{task_id}/task-grant", dependencies=[Depends(control_auth)])
    def task_grant(task_id: str, body: TaskGrantBody, response: Response) -> dict:
        task = queue.get(task_id)
        if task is None:
            raise HTTPException(status_code=404, detail="no such task")
        if (
            task.status not in Status.HELD
            or task.owner != body.worker_id
            or task.owner_session_id != body.owner_session_id
            or task.generation != body.expected_generation
        ):
            raise HTTPException(status_code=409, detail="task ownership incarnation changed")
        epoch = fencing_guard.current_epoch() if fencing_guard is not None else 0
        if epoch != body.expected_epoch:
            raise HTTPException(status_code=409, detail="task grant coordinator epoch changed")
        assert control_token is not None
        now = time.time()
        token = mint_task_scope_token(
            control_token, task_id=task_id, worker_id=body.worker_id,
            owner_session_id=body.owner_session_id, generation=task.generation,
            epoch=epoch, now=now,
        )
        response.headers["Cache-Control"] = "no-store"
        return {
            "task_id": task_id, "worker_id": body.worker_id,
            "owner_session_id": body.owner_session_id, "generation": task.generation,
            "epoch": epoch, "expires_at": now + DEFAULT_TTL_SECONDS, "task_token": token,
        }


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
    if (
        task.status not in Status.HELD
        or task.owner != scope.worker_id
        or task.owner_session_id != scope.owner_session_id
        or task.generation != scope.generation
    ):
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
    generation: int = 0,
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
        generation=generation,
    )
