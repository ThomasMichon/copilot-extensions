"""Control-authorized standing-charter routes over the native queue owner."""

from __future__ import annotations

from collections.abc import Callable

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, Field, StrictBool, StrictInt, StrictStr

from .assignment_workspace import AssignmentWorkspace
from .coordinator_auth import _make_control_auth
from .coordinator_task_models import AssignmentWorkspaceBody
from .coordinator_task_views import _task_dict
from .queue import TaskQueue
from .queue_common import Task
from .queue_records import TaskError
from .queue_standing_charters import StandingCharterDeclaration
from .queue_standing_recovery import StandingRecoveryGrant

STANDING_CAPABILITY_VERSION = 1
_Text = Field(min_length=1, max_length=1024)
_Revision = Field(ge=1, le=2**63 - 1)


class _Body(BaseModel):
    model_config = ConfigDict(extra="forbid")


class StandingDeclarationBody(_Body):
    namespace: StrictStr = _Text
    charter_key: StrictStr = _Text
    lane_key: StrictStr = _Text
    effort_ref: StrictStr = _Text
    materialization_ref: StrictStr = _Text
    source_revision: StrictStr = _Text
    expected_materialization_revision: StrictStr = _Text


class StandingReconcileBody(_Body):
    title: StrictStr = _Text
    repo: StrictStr = _Text
    declaration: StandingDeclarationBody
    expected_revision: StrictInt | None = Field(default=None, ge=1, le=2**63 - 1)


class StandingRevisionBody(_Body):
    expected_revision: StrictInt = _Revision


class StandingSourceEntryBody(_Body):
    title: StrictStr = _Text
    declaration: StandingDeclarationBody
    enabled: StrictBool = True


class StandingSourceSetBody(_Body):
    repo: StrictStr = _Text
    namespace: StrictStr = _Text
    entries: list[StandingSourceEntryBody]
    revision: StrictStr | None = Field(min_length=1, max_length=1024)
    expected_observation: StrictStr | None = Field(min_length=1, max_length=1024)


class StandingReserveBody(StandingRevisionBody):
    workspace: AssignmentWorkspaceBody


class StandingAcceptBody(StandingReserveBody):
    expected_generation: StrictInt = _Revision
    owner_session_id: StrictStr = _Text
    expected_head_revision: StrictInt = _Revision
    expected_owner_session_id: StrictStr | None = Field(default=None, min_length=1, max_length=1024)


class StandingCancelBody(StandingReserveBody):
    expected_generation: StrictInt = _Revision


class StandingReleaseBody(StandingReserveBody):
    expected_generation: StrictInt = _Revision
    owner_session_id: StrictStr = _Text
    expected_conclusion_revision: StrictInt = _Revision


class StandingRecoveryGrantBody(StandingReserveBody):
    expected_generation: StrictInt = _Revision
    owner_session_id: StrictStr = _Text
    grant_id: StrictStr = _Text
    authority_ref: StrictStr = _Text
    expires_at: float = Field(strict=True, gt=0, allow_inf_nan=False)
    max_attempts: StrictInt = Field(ge=1, le=2**31 - 1)


class StandingRecoveryRevokeBody(StandingRevisionBody):
    grant_id: StrictStr = _Text


class StandingRecoveryBatchBody(_Body):
    cursor: StrictStr | None = Field(default=None, min_length=1, max_length=1024)
    limit: StrictInt = Field(default=1, ge=1, le=10)


def standing_capability(queue: TaskQueue) -> dict:
    """Advertise standing operations; attachment needs the writer's own standing support."""
    from .assignment_local_standing import LocalStandingRelationshipWriter

    return {
        "version": STANDING_CAPABILITY_VERSION,
        "finite_child_link": 1,
        "source_set": 1,
        "attachment": queue.standing_attachment_supported(),
        "recovery_grants": 1,
        "retained_pairing_recovery": int(isinstance(
            queue._standing_relationship_writer, LocalStandingRelationshipWriter,
        )),
    }


def _workspace(body: AssignmentWorkspaceBody) -> AssignmentWorkspace:
    try:
        return AssignmentWorkspace.from_dict(body.model_dump())
    except TaskError as exc:
        raise HTTPException(status_code=400, detail=str(exc)) from exc


def register_standing_routes(
    app: FastAPI,
    queue: TaskQueue,
    *,
    control_token: str | None,
    emit_task: Callable[[str, dict], None],
    emit_event: Callable[[str, dict], None],
) -> None:
    control = [Depends(_make_control_auth(
        control_token, emit_event,
        operation="standing-charter",
        rejection_event="standing_charter.control_rejected",
        code_prefix="standing_charter_control",
        unavailable_message="standing charter operations require configured control authority",
        forbidden_message="invalid or missing standing charter control authority",
    ))]

    def mutate(action: str, op: Callable[[], Task], task_id: str | None = None) -> dict:
        try:
            task = op()
        except TaskError as exc:
            missing = task_id is not None and queue.get(task_id) is None
            raise HTTPException(
                status_code=404 if missing else 409, detail="no such task" if missing else str(exc),
            ) from exc
        result = _task_dict(task)
        emit_task(f"task.standing_{action}", result)
        return result

    @app.post("/standing-charters", dependencies=control)
    def reconcile(body: StandingReconcileBody) -> dict:
        try:
            declaration = StandingCharterDeclaration(**body.declaration.model_dump())
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return mutate("reconciled", lambda: queue.reconcile_standing_charter(
            body.title, declaration, repo=body.repo, expected_revision=body.expected_revision,
        ))

    @app.get("/standing-charters/source-repositories", dependencies=control)
    def source_repositories(namespace: str) -> dict:
        try:
            return {"repositories": queue.standing_source_repositories(namespace)}
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/standing-charters/source-sets", dependencies=control)
    def source_observation(repo: str, namespace: str) -> dict:
        try:
            return {"observation": queue.standing_source_observation(repo, namespace)}
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/standing-charters/source-sets", dependencies=control)
    def reconcile_source_set(body: StandingSourceSetBody) -> dict:
        try:
            result = queue.reconcile_standing_source_set(
                body.repo, body.namespace, [entry.model_dump() for entry in body.entries],
                revision=body.revision, expected_observation=body.expected_observation,
            )
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        for task_id in (*result["enrolled"], *result["changed"]):
            emit_task("task.standing_reconciled", _task_dict(queue.get(task_id)))
        for retired in result["retired"]:
            emit_task("task.standing_retiring", _task_dict(queue.get(retired["id"])))
        return result

    @app.post("/tasks/{task_id}/standing/reserve", dependencies=control)
    def reserve(task_id: str, body: StandingReserveBody) -> dict:
        workspace = _workspace(body.workspace)
        return mutate("reserved", lambda: queue.reserve_standing_charter(
            task_id, workspace, expected_revision=body.expected_revision,
        ), task_id)

    @app.post("/tasks/{task_id}/standing/accept", dependencies=control)
    def accept(task_id: str, body: StandingAcceptBody) -> dict:
        workspace = _workspace(body.workspace)
        return mutate("accepted", lambda: queue.accept_standing_charter(
            task_id, workspace, expected_revision=body.expected_revision,
            expected_generation=body.expected_generation,
            owner_session_id=body.owner_session_id,
            expected_head_revision=body.expected_head_revision,
            expected_owner_session_id=body.expected_owner_session_id,
        ), task_id)

    @app.post("/tasks/{task_id}/standing/cancel", dependencies=control)
    def cancel(task_id: str, body: StandingCancelBody) -> dict:
        workspace = _workspace(body.workspace)
        return mutate("cancelled", lambda: queue.cancel_standing_reservation(
            task_id, workspace, expected_revision=body.expected_revision,
            expected_generation=body.expected_generation,
        ), task_id)

    @app.post("/tasks/{task_id}/standing/release", dependencies=control)
    def release(task_id: str, body: StandingReleaseBody) -> dict:
        workspace = _workspace(body.workspace)
        return mutate("released", lambda: queue.release_standing_attachment(
            task_id, workspace, expected_revision=body.expected_revision,
            expected_generation=body.expected_generation,
            owner_session_id=body.owner_session_id,
            expected_conclusion_revision=body.expected_conclusion_revision,
        ), task_id)

    @app.post("/tasks/{task_id}/standing/recovery-grant", dependencies=control)
    def recovery_grant(task_id: str, body: StandingRecoveryGrantBody) -> dict:
        workspace = _workspace(body.workspace)
        try:
            grant = StandingRecoveryGrant(
                body.grant_id, body.authority_ref, body.expires_at, body.max_attempts,
            )
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        return mutate("recovery_granted", lambda: queue.grant_standing_recovery(
            task_id, workspace, grant, expected_revision=body.expected_revision,
            expected_generation=body.expected_generation, owner_session_id=body.owner_session_id,
        ), task_id)

    @app.post("/tasks/{task_id}/standing/recovery-revoke", dependencies=control)
    def recovery_revoke(task_id: str, body: StandingRecoveryRevokeBody) -> dict:
        return mutate("recovery_revoked", lambda: queue.revoke_standing_recovery(
            task_id, grant_id=body.grant_id, expected_revision=body.expected_revision,
        ), task_id)

    @app.post("/standing/recovery/reconcile", dependencies=control)
    def recovery_reconcile(body: StandingRecoveryBatchBody, request: Request) -> dict:
        from .standing_recovery_driver import recover

        gate = request.app.state.drain_gate
        with gate.track_claim():
            if gate.draining:
                raise HTTPException(409, "standing recovery is inhibited while draining")
            if standing_capability(queue)["retained_pairing_recovery"] != 1:
                raise HTTPException(409, "coordinator lacks supported native retained pairing recovery")
            with queue._connect() as conn:
                rows = conn.execute(
                    "SELECT id FROM tasks WHERE kind='standing-charter' AND id>? AND "
                    "json_extract(standing_charter,'$.recovery') IS NOT NULL "
                    "ORDER BY id LIMIT ?", (body.cursor or "", body.limit),
                ).fetchall()
            reports = []
            for row in rows:
                try:
                    reports.append(recover(queue, row["id"]))
                except TaskError as exc:
                    reports.append({"task_id": row["id"], "state": "held", "reason": str(exc)})
        return {
            "version": 1, "reports": reports,
            "next_cursor": rows[-1]["id"] if len(rows) == body.limit else None,
        }

    lifecycle = {
        "retire": ("retiring", queue.retire_standing_charter),
        "settle-retirement": ("retired", queue.settle_standing_retirement),
        "reenroll": ("reenrolled", queue.reenroll_standing_charter),
    }
    def lifecycle_route(action: str, method: Callable[..., Task]):
        def change(task_id: str, body: StandingRevisionBody) -> dict:
            return mutate(action, lambda: method(task_id, expected_revision=body.expected_revision), task_id)

        return change

    for route, (action, method) in lifecycle.items():
        app.add_api_route(
            f"/tasks/{{task_id}}/standing/{route}", lifecycle_route(action, method),
            methods=["POST"], dependencies=control, name=f"standing_{route.replace('-', '_')}",
        )
