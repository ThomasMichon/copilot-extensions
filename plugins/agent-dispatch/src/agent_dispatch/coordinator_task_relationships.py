"""Original-request observations and control-authorized relationship reconciliation."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Query

from .coordinator_auth import _make_control_auth
from .coordinator_task_models import RelationshipReconcileBody
from .queue import TaskQueue
from .queue_records import TaskError


def register_task_relationship_routes(
    app: FastAPI,
    queue: TaskQueue,
    *,
    control_token: str | None,
    emit_event: Callable[[str, dict], None],
) -> None:
    @app.get("/tasks/{task_id}/relationship")
    def get_task_relationship(
        task_id: str,
        event_id: int | None = Query(None, ge=1, le=2**63 - 1),
    ) -> dict | None:
        if queue.get(task_id) is None:
            raise HTTPException(404, detail="no such task")
        return queue.task_relationship(task_id, event_id=event_id)

    @app.post(
        "/tasks/{task_id}/relationship/reconcile",
        dependencies=[
            Depends(
                _make_control_auth(
                    control_token,
                    emit_event,
                    operation="task-relationship-reconcile",
                    rejection_event="task_relationship.reconcile_rejected",
                    code_prefix="task_relationship_control",
                    unavailable_message=(
                        "task relationship reconciliation requires configured control authority"
                    ),
                    forbidden_message="invalid or missing task relationship control authority",
                )
            )
        ],
    )
    def reconcile_task_relationship(task_id: str, body: RelationshipReconcileBody) -> dict:
        try:
            return queue.reconcile_task_relationship(task_id, event_id=body.event_id)
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
