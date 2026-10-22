"""Coordinator spawn-reservation and routing-assignment routes."""

from __future__ import annotations

from dataclasses import asdict
import logging
import sqlite3
import subprocess

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import BaseModel, ConfigDict, FiniteFloat, Field

from .events import EventBus
from .queue import RoutingAssignment, SpawnReservation, TaskError, TaskQueue


class ReserveSpawnBody(BaseModel):
    task_id: str
    reserved_by: str | None = None
    allow_suspended_reembodiment: bool = False
    driver: str | None = None
    allocation_revision: int | None = Field(default=None, ge=1, le=2**63 - 1, strict=True)


class RoutingAssignmentBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    purpose: str
    selected_model: str
    eligibility_state: str
    selection_reason: str
    execution_surface: str
    decision_ref: str
    parent_assignment_id: str | None = None
    containment_profile_ref: str | None = None
    trial_ref: str | None = None
    coordinator_session_ref: str | None = None


class RoutingTransitionBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_type: str
    actor_role: str
    terminal_disposition: str | None = None
    reason_code: str | None = None
    worker_session_ref: str | None = None


class RoutingBillingRefBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    event_id: str
    provider: str
    provider_billing_event_ref: str
    actor_role: str
    occurred_at: FiniteFloat | None = None


class RecordSpawnBody(BaseModel):
    session_handle: str | None = None
    worktree: str | None = None
    session_info: dict | None = None
    startup_complete: bool | None = Field(default=None, strict=True)
    startup_revision: int | None = Field(default=None, ge=1, strict=True)

class FenceSpawnStartupBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    startup_revision: int = Field(ge=1, strict=True)


class ClaimSpawnCoolingBody(FenceSpawnStartupBody):
    session_handle: str = Field(min_length=1)
    state: str


class RecordSpawnWorktreeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worktree: str
    ownership: str = "unknown"
    creating_host: str | None = None
    driver: str | None = None
    allocation_project: str | None = None
    worktree_path: str | None = None


class RecordSpawnAllocationBody(BaseModel):
    model_config = ConfigDict(extra="forbid")
    project: str = Field(min_length=1)


class ReservationDetailBody(BaseModel):
    detail: str | None = None
    conclusion_state: str | None = None
    conclusion_detail: str | None = None
    claim_token: str | None = None
    release_requested: bool = False


class FailSpawnBody(ReservationDetailBody):
    force: bool = False
    confirmed_absent: bool = False


class DeferSpawnBody(ReservationDetailBody):
    expected_generation: int | None = Field(default=None, ge=0, le=2**63 - 1, strict=True)
    startup_revision: int | None = Field(default=None, ge=1, strict=True)
    cooling_token: str | None = Field(default=None, min_length=1)


class RecordColdBody(BaseModel):
    release_exclusive: bool = False
    startup_revision: int | None = Field(default=None, ge=1, strict=True)
    cooling_token: str | None = Field(default=None, min_length=1)


class RequestSpawnReleaseBody(BaseModel):
    detail: str | None = None
    disposition: str = "failed"
    session_handle: str | None = None
    worktree: str | None = None
    finished_startup_revision: int | None = Field(default=None, ge=1, strict=True)


class RetireSpawnBody(ReservationDetailBody):
    exact_absence: bool = False


class ValidateConclusionClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    claim_token: str
    claimant_machine: str | None = None


class ClaimConclusionBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)

    claimant_machine: str | None = None


class RecordConclusionBody(ReservationDetailBody):
    model_config = ConfigDict(extra="forbid", strict=True)

    claimant_machine: str | None = None


class RearmSpawnBody(BaseModel):
    permitted: bool = False
    reason: str | None = None
    min_failures: int = 3


class AuthorizeFreshBody(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True)
    reservation_key: str
    worktree: str
    expected_generation: int
    permitted: bool = False
    confirmed_no_live_holder: bool = False
    reason: str


def _reservation_dict(res: SpawnReservation) -> dict:
    return asdict(res)


def register_spawn_routes(
    app: FastAPI,
    queue: TaskQueue,
    bus: EventBus,
    control_token: str | None = None,
    *,
    restricted_ingress_authenticated: bool = False,
) -> None:
    from .coordinator_auth import _make_control_auth
    from .coordinator_execution_runtime import coordinator_incarnation

    incarnation = coordinator_incarnation(app)

    def rejected(event: str, detail: dict[str, object]) -> None:
        logging.getLogger("agent-dispatch").warning("%s: %s", event, detail)
    control_auth = _make_control_auth(control_token, rejected)
    from .coordinator_cleanup_recovery import register as register_cleanup_recovery
    register_cleanup_recovery(app, queue, bus, control_auth)

    @app.post(
        "/spawn-reservations/tasks/{task_id}/authorize-fresh",
        dependencies=[Depends(control_auth)],
    )
    def authorize_fresh(task_id: str, body: AuthorizeFreshBody) -> dict:
        try:
            from .acquisition_receipts import verify_recovery_bridge
            verify_recovery_bridge()
            return queue.authorize_fresh_acquisition(task_id, **body.model_dump())
        except TaskError as exc:
            raise HTTPException(409, detail=str(exc)) from exc
        except (OSError, subprocess.TimeoutExpired) as exc:
            raise HTTPException(503, detail="recovery bridge readiness unavailable") from exc

    def _require_task(task_id: str) -> None:
        if queue.get(task_id) is None:
            raise HTTPException(status_code=404, detail="no such task")

    def _reservation_guard(op) -> dict:
        try:
            return _reservation_dict(op())
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such reservation") else 409
            raise HTTPException(status_code=status, detail=msg) from exc

    def _routing_guard(op) -> tuple[RoutingAssignment, bool]:
        try:
            return op()
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such") else 409
            raise HTTPException(status_code=status, detail=msg) from exc

    @app.post("/spawn-reservations")
    @app.post("/spawn-reservations/revisions")
    def reserve_spawn(body: ReserveSpawnBody, request: Request) -> dict:
        if request.url.path.endswith("/revisions") and body.allocation_revision is None:
            raise HTTPException(status_code=422, detail="explicit allocation revision is required")
        _require_task(body.task_id)
        try:
            reservation, reserved = queue.reserve_spawn(
                body.task_id,
                reserved_by=body.reserved_by,
                allow_suspended_reembodiment=body.allow_suspended_reembodiment,
                driver=body.driver,
                allocation_revision=body.allocation_revision,
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        result = _reservation_dict(reservation)
        task = queue.get(body.task_id)
        if reserved and task and task.source == "ci-repair":
            # CI bodies launch inside the trusted coordinator, never the
            # caller-selected supervisor factory. Return no launch permission.
            from .ci_repair_spawn import make_ci_repair_spawn
            from .coordinator_execution_sender import CoordinatorExecutionSender
            sender = CoordinatorExecutionSender(queue, task, reservation, incarnation)
            registration = queue.get_registration(task.origin_ref)
            spec = registration.spec if registration else {}
            try:
                if not registration or registration.status != "active":
                    raise ValueError("CI repair registration is no longer active")
                spawn = make_ci_repair_spawn(
                    spec["ci_repair_bindings"],
                    admission_command=spec["ci_repair_admission_command"],
                    route=" --shared" if spec.get("shared") is True else "",
                    execution_sender=sender,
                )
                ok, handle = spawn(asdict(task))
            except (TaskError, ValueError, KeyError, OSError) as exc:
                ok, handle = False, {"error": str(exc)}
            if ok:
                queue.record_staging_spawn(reservation.key, handle["session"])
                from .ci_repair_spawn import authority_snapshot, reservation_authorized
                with queue._connect() as probe:
                    expected = authority_snapshot(probe, task)
                    ok = reservation_authorized(probe, task, task.origin_ref, transition={
                        "phase": "spawned", "session_handle": handle["session"],
                    })
                if not ok:
                    queue.request_spawn_release(reservation.key, session_handle=handle["session"], detail="spawned identity admission refused")
                    return {"reserved": False, "reservation": _reservation_dict(queue.get_reservation(reservation.key)),
                            "coordinator_launch": {"ok": False, "error": "spawned identity admission refused", **handle}}
                try:
                    queue.record_spawn(reservation.key, session_handle=handle["session"], authority_expected=expected)
                except TaskError:
                    queue.request_spawn_release(reservation.key, session_handle=handle["session"], detail="authority changed before publication")
                    return {"reserved": False, "reservation": _reservation_dict(queue.get_reservation(reservation.key)),
                            "coordinator_launch": {"ok": False, "error": "authority changed"}}
                bus.publish({"type": "spawn.spawned", "reservation": _reservation_dict(queue.get_reservation(reservation.key))})
                from . import bridge
                delivery_error = "admitted task turn delivery failed"
                try:
                    with sender.attempt("deliver") as attempt:
                        delivered = bridge.resume_worker(handle["session"].removeprefix("local-body:"), handle["task_seed"], timeout=30)
                        if delivered:
                            attempt.sent()
                except (TaskError, OSError, sqlite3.Error, subprocess.TimeoutExpired) as exc:
                    delivered = False
                    delivery_error = str(exc)
                if not delivered:
                    queue.request_spawn_release(reservation.key, detail=delivery_error)
                    return {"reserved": False, "reservation": _reservation_dict(queue.get_reservation(reservation.key)),
                            "coordinator_launch": {"ok": False, "error": delivery_error}}
                queue.record_spawn(
                    reservation.key, session_handle=handle["session"], startup_complete=True,
                    startup_revision=reservation.startup_revision,
                )
            else:
                if handle.get("session"):
                    queue.request_spawn_release(
                        reservation.key,
                        session_handle=handle["session"],
                        detail=handle.get("error"),
                    )
                else:
                    queue.fail_spawn(reservation.key, detail=handle.get("error"))
            return {"reserved": False, "reservation": _reservation_dict(queue.get_reservation(reservation.key)),
                    "coordinator_launch": {"ok": ok, **handle}}
        if reserved:
            bus.publish({"type": "spawn.reserved", "reservation": result})
        return {"reserved": reserved, "reservation": result}

    @app.post("/spawn-reservations/{key}/routing-assignment")
    def record_routing_assignment(key: str, body: RoutingAssignmentBody) -> dict:
        assignment, created = _routing_guard(
            lambda: queue.record_routing_assignment(key, body.model_dump())
        )
        result = asdict(assignment)
        if created:
            bus.publish({"type": "routing.assigned", "assignment": result})
        return {"created": created, "assignment": result}

    @app.post("/routing-assignments/{assignment_id}/transition")
    def transition_routing_assignment(
        assignment_id: str,
        body: RoutingTransitionBody,
    ) -> dict:
        assignment, changed = _routing_guard(
            lambda: queue.transition_routing_assignment(
                assignment_id,
                body.event_type,
                body.actor_role,
                terminal_disposition=body.terminal_disposition,
                reason_code=body.reason_code,
                worker_session_ref=body.worker_session_ref,
            )
        )
        result = asdict(assignment)
        if changed:
            bus.publish({"type": "routing.transitioned", "assignment": result})
        return {"changed": changed, "assignment": result}

    @app.post("/routing-assignments/{assignment_id}/billing-ref")
    def record_routing_billing_ref(
        assignment_id: str,
        body: RoutingBillingRefBody,
    ) -> dict:
        try:
            created = queue.record_routing_billing_ref(
                assignment_id,
                body.event_id,
                body.provider,
                body.provider_billing_event_ref,
                body.actor_role,
                occurred_at=body.occurred_at,
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        if created:
            bus.publish({
                "type": "routing.billing_linked",
                "assignment_id": assignment_id,
                "event_id": body.event_id,
            })
        return {"created": created, "assignment_id": assignment_id}

    @app.get("/routing-assignments")
    def list_routing_assignments(
        task_id: str | None = None,
        limit: int = 100,
    ) -> list[dict]:
        return [
            asdict(assignment)
            for assignment in queue.list_routing_assignments(
                task_id=task_id,
                limit=limit,
            )
        ]

    @app.get("/routing-assignments/{assignment_id}/events")
    def get_routing_assignment_events(assignment_id: str) -> list[dict]:
        try:
            return queue.routing_assignment_events(assignment_id)
        except TaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.get("/routing-assignments/{assignment_id}")
    def get_routing_assignment(assignment_id: str) -> dict:
        assignment = queue.get_routing_assignment(assignment_id)
        if assignment is None:
            raise HTTPException(status_code=404, detail="no such routing assignment")
        return asdict(assignment)

    @app.post("/spawn-reservations/{key}/spawned")
    def record_spawn(key: str, body: RecordSpawnBody) -> dict:
        if body.session_info is not None:
            from .ordinary_bridge import record_session_info
            _reservation_guard(lambda: record_session_info(queue, key, body.session_info))
        reservation = queue.get_reservation(key)
        task = queue.get(reservation.task_id) if reservation else None
        if task and task.source == "ci-repair":
            from .ci_repair_spawn import reservation_authorized
            with queue._connect() as conn:
                if not reservation_authorized(conn, task, task.origin_ref, transition={
                    "phase": "spawned", "session_handle": body.session_handle,
                }):
                    raise HTTPException(status_code=403, detail="CI repair spawned identity not independently verified")
        result = _reservation_guard(
            lambda: queue.record_spawn(
                key, session_handle=body.session_handle, worktree=body.worktree,
                startup_complete=body.startup_complete,
                startup_revision=body.startup_revision,
            )
        )
        bus.publish({"type": "spawn.spawned", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/startup-fence")
    def fence_spawn_startup(key: str, body: FenceSpawnStartupBody) -> dict:
        result = _reservation_guard(
            lambda: queue.fence_spawn_startup(key, startup_revision=body.startup_revision)
        )
        bus.publish({"type": "spawn.startup_fenced", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/claim-cooling")
    def claim_spawn_cooling(key: str, body: ClaimSpawnCoolingBody) -> dict:
        result = _reservation_guard(lambda: queue.claim_spawn_cooling(
            key, startup_revision=body.startup_revision,
            session_handle=body.session_handle, state=body.state,
        ))
        bus.publish({"type": "spawn.cooling_claimed", "reservation": result})
        return result

    from .ordinary_bridge import register_routes
    register_routes(app, queue, bus)
    from .restricted_delivery import register_routes as register_restricted_routes
    register_restricted_routes(
        app, queue, bus, control_token, authenticated_ingress=restricted_ingress_authenticated,
    )
    @app.post("/spawn-reservations/{key}/allocation")
    def record_spawn_allocation(key: str, body: RecordSpawnAllocationBody) -> dict:
        result = _reservation_guard(lambda: queue.record_spawn_allocation(key, body.project))
        bus.publish({"type": "spawn.allocation_bound", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/worktree")
    def record_spawn_worktree(key: str, body: RecordSpawnWorktreeBody) -> dict:
        result = _reservation_guard(
            lambda: queue.record_spawn_worktree(
                key,
                body.worktree,
                ownership=body.ownership,
                creating_host=body.creating_host,
                driver=body.driver,
                allocation_project=body.allocation_project,
                worktree_path=body.worktree_path,
            )
        )
        bus.publish({"type": "spawn.worktree_recorded", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/release")
    def request_spawn_release(key: str, body: RequestSpawnReleaseBody) -> dict:
        result = _reservation_guard(
            lambda: queue.request_spawn_release(key, **body.model_dump())
        )
        bus.publish({"type": "spawn.release_requested", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/retire")
    def retire_spawn(key: str, body: RetireSpawnBody) -> dict:
        result = _reservation_guard(
            lambda: queue.retire_spawn(
                key,
                exact_absence=body.exact_absence,
                detail=body.detail,
                conclusion_state=body.conclusion_state,
                conclusion_detail=body.conclusion_detail,
            )
        )
        bus.publish({"type": "spawn.retired", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/fail")
    def fail_spawn(key: str, body: FailSpawnBody) -> dict:
        result = _reservation_guard(
            lambda: queue.fail_spawn(
                key,
                detail=body.detail,
                conclusion_state=body.conclusion_state,
                conclusion_detail=body.conclusion_detail,
                claim_token=body.claim_token,
                force=body.force,
                confirmed_absent=body.confirmed_absent,
                release_requested=body.release_requested,
            )
        )
        bus.publish({"type": "spawn.failed", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/defer")
    def defer_spawn(key: str, body: DeferSpawnBody) -> dict:
        result = _reservation_guard(lambda: queue.defer_spawn(
            key, detail=body.detail,
            **({"expected_generation": body.expected_generation}
               if body.expected_generation is not None else {}),
            startup_revision=body.startup_revision, cooling_token=body.cooling_token,
        ))
        bus.publish({"type": "spawn.deferred", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/cold")
    def record_cold(key: str, body: RecordColdBody = RecordColdBody()) -> dict:
        result = _reservation_guard(
            lambda: queue.record_cold(
                key, release_exclusive=body.release_exclusive,
                startup_revision=body.startup_revision,
                cooling_token=body.cooling_token,
            )
        )
        bus.publish({"type": "spawn.cold", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/settle")
    def settle_spawn(key: str, body: ReservationDetailBody) -> dict:
        result = _reservation_guard(
            lambda: queue.settle_spawn(
                key,
                detail=body.detail,
                conclusion_state=body.conclusion_state,
                conclusion_detail=body.conclusion_detail,
                claim_token=body.claim_token,
                release_requested=body.release_requested,
            )
        )
        bus.publish({"type": "spawn.settled", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/conclusion")
    def record_spawn_conclusion(key: str, body: RecordConclusionBody) -> dict:
        if not body.conclusion_state or body.conclusion_detail is None:
            raise HTTPException(
                status_code=400,
                detail="conclusion_state and conclusion_detail are required",
            )
        result = _reservation_guard(
            lambda: queue.record_spawn_conclusion(
                key,
                conclusion_state=body.conclusion_state or "",
                conclusion_detail=body.conclusion_detail or "",
                detail=body.detail,
                claim_token=body.claim_token,
                claimant_machine=body.claimant_machine,
            )
        )
        bus.publish({"type": "spawn.conclusion", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/conclusion/claim")
    def claim_spawn_conclusion_retry(key: str, body: ClaimConclusionBody | None = None) -> dict:
        body = body if body is not None else ClaimConclusionBody()
        try:
            reservation, claimed, claim_token = (
                queue.claim_spawn_conclusion_retry(key, claimant_machine=body.claimant_machine)
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such reservation") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        result = {
            "claimed": claimed,
            "claim_token": claim_token,
            "reservation": _reservation_dict(reservation),
        }
        bus.publish({"type": "spawn.conclusion_claimed", **result})
        return result

    @app.post("/spawn-reservations/{key}/conclusion/validate")
    def validate_spawn_conclusion_claim(
        key: str,
        body: ValidateConclusionClaimBody,
    ) -> dict:
        result = _reservation_guard(
            lambda: queue.validate_spawn_conclusion_claim(
                key,
                body.claim_token,
                claimant_machine=body.claimant_machine,
            )
        )
        return result

    @app.post("/spawn-reservations/tasks/{task_id}/rearm")
    def rearm_spawn(task_id: str, body: RearmSpawnBody) -> dict:
        try:
            result = queue.rearm_spawn(
                task_id,
                permitted=body.permitted,
                reason=body.reason,
                min_failures=body.min_failures,
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        bus.publish({"type": "spawn.rearmed", "rearm": result})
        return result

    @app.get("/spawn-reservations")
    def list_reservations(
        task_id: str | None = None,
        state: str | None = None,
        repo: str | None = None,
        label: str | None = None,
        conclusion_state: str | None = None,
        resume_requested: bool | None = None,
        task_status: str | None = None,
        latest_only: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        states = (
            [s.strip() for s in state.split(",") if s.strip()] if state else None
        )
        task_statuses = (
            [s.strip() for s in task_status.split(",") if s.strip()]
            if task_status
            else None
        )
        return [
            _reservation_dict(r)
            for r in queue.list_reservations(
                task_id=task_id,
                state=states,
                repo=repo,
                label=label,
                conclusion_state=conclusion_state,
                resume_requested=resume_requested,
                task_status=task_statuses,
                latest_only=latest_only,
                limit=limit,
            )
        ]

    @app.get("/spawn-reservations/{key}")
    def get_reservation(key: str) -> dict:
        reservation = queue.get_reservation(key)
        if reservation is None:
            raise HTTPException(status_code=404, detail="no such reservation")
        from .ordinary_bridge import legacy_bound
        from .ordinary_agent_storage import reservation_native_facts
        return {
            **_reservation_dict(reservation),
            "legacy_acquisition": legacy_bound(queue, reservation.task_id, reservation.worktree),
            **reservation_native_facts(queue, key),
        }
