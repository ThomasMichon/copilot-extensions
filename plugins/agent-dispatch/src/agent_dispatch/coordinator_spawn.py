"""Coordinator spawn-reservation and routing-assignment routes."""

from __future__ import annotations

from dataclasses import asdict

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, ConfigDict, FiniteFloat

from .events import EventBus
from .queue import RoutingAssignment, SpawnReservation, TaskError, TaskQueue


class ReserveSpawnBody(BaseModel):
    task_id: str
    reserved_by: str | None = None
    allow_suspended_reembodiment: bool = False


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


class RecordSpawnWorktreeBody(BaseModel):
    worktree: str
    ownership: str = "unknown"
    creating_host: str | None = None
    driver: str | None = None


class ReservationDetailBody(BaseModel):
    detail: str | None = None
    conclusion_state: str | None = None
    conclusion_detail: str | None = None
    claim_token: str | None = None


class RequestSpawnReleaseBody(BaseModel):
    detail: str | None = None
    disposition: str = "failed"
    session_handle: str | None = None
    worktree: str | None = None


class RetireSpawnBody(ReservationDetailBody):
    exact_absence: bool = False


class ValidateConclusionClaimBody(BaseModel):
    claim_token: str


class RearmSpawnBody(BaseModel):
    permitted: bool = False
    reason: str | None = None
    min_failures: int = 3


def _reservation_dict(res: SpawnReservation) -> dict:
    return asdict(res)


def register_spawn_routes(
    app: FastAPI,
    queue: TaskQueue,
    bus: EventBus,
) -> None:
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
    def reserve_spawn(body: ReserveSpawnBody) -> dict:
        _require_task(body.task_id)
        try:
            reservation, reserved = queue.reserve_spawn(
                body.task_id,
                reserved_by=body.reserved_by,
                allow_suspended_reembodiment=body.allow_suspended_reembodiment,
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        result = _reservation_dict(reservation)
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
        result = _reservation_guard(
            lambda: queue.record_spawn(
                key, session_handle=body.session_handle, worktree=body.worktree
            )
        )
        bus.publish({"type": "spawn.spawned", "reservation": result})
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
    def fail_spawn(key: str, body: ReservationDetailBody) -> dict:
        result = _reservation_guard(
            lambda: queue.fail_spawn(
                key,
                detail=body.detail,
                conclusion_state=body.conclusion_state,
                conclusion_detail=body.conclusion_detail,
                claim_token=body.claim_token,
            )
        )
        bus.publish({"type": "spawn.failed", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/defer")
    def defer_spawn(key: str, body: ReservationDetailBody) -> dict:
        result = _reservation_guard(lambda: queue.defer_spawn(key, detail=body.detail))
        bus.publish({"type": "spawn.deferred", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/cold")
    def record_cold(key: str) -> dict:
        result = _reservation_guard(lambda: queue.record_cold(key))
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
            )
        )
        bus.publish({"type": "spawn.settled", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/conclusion")
    def record_spawn_conclusion(key: str, body: ReservationDetailBody) -> dict:
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
            )
        )
        bus.publish({"type": "spawn.conclusion", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/conclusion/claim")
    def claim_spawn_conclusion_retry(key: str) -> dict:
        try:
            reservation, claimed, claim_token = (
                queue.claim_spawn_conclusion_retry(key)
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
        limit: int = 200,
    ) -> list[dict]:
        states = (
            [s.strip() for s in state.split(",") if s.strip()] if state else None
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
                limit=limit,
            )
        ]

    @app.get("/spawn-reservations/{key}")
    def get_reservation(key: str) -> dict:
        reservation = queue.get_reservation(key)
        if reservation is None:
            raise HTTPException(status_code=404, detail="no such reservation")
        return _reservation_dict(reservation)
