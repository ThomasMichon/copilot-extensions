"""Accepted-original-owner authority, restricted to relevance closeout routes."""

from __future__ import annotations

from fastapi import HTTPException, Request

from .lease import FencingGuard
from .queue import TaskError, TaskQueue
from .queue_relevance_acknowledgement import _replay
from .queue_relevance_binding import closeout_detail
from .queue_relevance_readiness import _state

CLOSEOUT_PATH = "/tasks/{task_id}/relevance-closeout"
CLOSEOUT_ROUTES = frozenset(
    {
        ("GET", CLOSEOUT_PATH),
        ("POST", f"{CLOSEOUT_PATH}/prepare"),
        ("POST", f"{CLOSEOUT_PATH}/finish"),
    }
)


def resolve_closeout_authority(
    request: Request,
    queue: TaskQueue,
    fencing_guard: FencingGuard | None,
    task_id: str,
    arguments: dict | None = None,
) -> dict:
    """Recheck captured ownership or exact terminal replay, never generic task authority."""
    scope = getattr(request.state, "task_scope", None)
    if scope is not None:
        if scope.task_id != task_id:
            raise HTTPException(403, detail="task-scoped token is not valid for this task")
        if fencing_guard is not None and not fencing_guard.is_valid(scope.epoch):
            raise HTTPException(
                403, detail="task-scoped token's coordinator epoch has been superseded"
            )
    try:
        with queue._connect() as conn:
            conn.execute("BEGIN")
            row = conn.execute(
                "SELECT * FROM task_relevance_closeouts WHERE task_id=?",
                (task_id,),
            ).fetchone()
            if row is None:
                raise TaskError("no accepted relevance closeout")
            captured = {
                "request_id": row["request_id"],
                "worker_id": row["owner"],
                "native_id": row["owner_session_id"],
                "expected_generation": row["generation"],
            }
            if scope is not None and (
                scope.worker_id,
                scope.owner_session_id,
                scope.generation,
            ) != (row["owner"], row["owner_session_id"], row["generation"]):
                raise HTTPException(
                    403, detail="task-scoped token does not match the closeout owner"
                )
            if arguments is not None and arguments != captured:
                raise TaskError("relevance closeout arguments do not match the captured owner")
            if _replay(queue, conn, task_id, captured) is None:
                _state(queue, conn, task_id, captured)
            return closeout_detail(row)
    except TaskError as exc:
        raise HTTPException(403 if scope is not None else 409, detail=str(exc)) from exc
