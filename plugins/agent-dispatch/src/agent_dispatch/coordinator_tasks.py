"""Coordinator task CRUD, claim, and governance routes."""

from __future__ import annotations

import asyncio
from collections.abc import Callable
from dataclasses import asdict
from typing import Annotated, Any

from fastapi import Depends, FastAPI, HTTPException, Request
from pydantic import (
    BaseModel,
    BeforeValidator,
    ConfigDict,
    Field,
    FiniteFloat,
    StrictInt,
)
from pydantic_core import PydanticCustomError

from . import telemetry
from .coordinator_auth import _make_control_auth
from .coordinator_loops import DrainGate, DrainRequest
from .events import EventBus
from .queue import (
    CompletionOutcome,
    ProducerFenceError,
    ProducerScopeValidationError,
    ResultTooLargeError,
    ResultValidationError,
    StructuredResult,
    Task,
    TaskError,
    TaskQueue,
    encode_result,
    worker_id_for,
)


def _strict_structured_result(value: Any) -> Any:
    if value is None:
        return value
    try:
        encode_result(value)
    except ResultTooLargeError as exc:
        raise PydanticCustomError("result_too_large", str(exc)) from exc
    except ResultValidationError as exc:
        raise ValueError(str(exc)) from exc
    return value


HttpStructuredResult = Annotated[
    StructuredResult, BeforeValidator(_strict_structured_result)
]


class ProducerScopeBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    source: str


class CreateBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    title: str
    repo: str | None = None
    prompt: str = ""
    proposed: bool = False
    requires: list[str] = Field(default_factory=list)
    excludes: list[str] = Field(default_factory=list)
    affinity: dict[str, str] = Field(default_factory=dict)
    labels: list[str] = Field(default_factory=list)
    payload_ref: str | None = None
    payload_inline: str | None = None
    target_machine: str | None = None
    target_worktree: str | None = None
    target_repo: str | None = None
    exclusive_key: str | None = None
    supersede_exclusive_key: bool = False
    source: str | None = None
    origin_ref: str | None = None
    evaluator_ref: str | None = None
    dedup_key: str | None = None
    producer_scope: ProducerScopeBody | None = None
    producer_id: str | None = None
    producer_generation: StrictInt | None = None
    producer_capability: str | None = None
    producer_request_id: str | None = None
    goal: str | None = None
    done_criteria: str | None = None
    not_before: FiniteFloat = 0.0
    claim_as: str | None = None


class ProducerScopeHandoffBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    repo: str
    source: str
    producer_id: str
    expected_generation: StrictInt
    required_label: str | None = None


class ClaimBody(BaseModel):
    model_config = ConfigDict(extra="forbid")

    worker_id: str | None = None
    repo: str | None = None
    all_repos: bool = False
    machine: str | None = None
    worktree: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    task_id: str | None = None
    lease_seconds: int | None = None
    evaluation: bool = False


class WorkerBody(BaseModel):
    worker_id: str
    owner_session_id: str | None = None


class ActivityBody(BaseModel):
    activity: str | None = None
    reservation_key: str


class OwnerSessionBody(BaseModel):
    worker_id: str
    owner_session_id: str
    expected_generation: int | None = None


class YieldBody(BaseModel):
    worker_id: str
    note: str | None = None
    exclude: str | None = None
    release_spawn: bool = True


class SuspendBody(BaseModel):
    worker_id: str
    reason: str
    expected_status: str | None = None
    expected_generation: int | None = None
    expected_owner_session_id: str | None = None
    reject_pending_steer: bool = True


class ResumeBody(BaseModel):
    worker_id: str
    wake: bool = True
    message: str | None = None
    adopt_session: bool = False
    adopt_owner_session_id: str | None = None
    reuse_session: bool = False
    expected_owner_session_id: str | None = None
    expected_generation: int | None = None


class ReleaseBody(BaseModel):
    worker_id: str
    reason: str | None = None


class CompleteBody(BaseModel):
    worker_id: str
    result_ref: str | None = None
    result: HttpStructuredResult = None  # type: ignore[assignment]
    expected_status: str | None = None
    expected_owner_session_id: str | None = None
    expected_generation: int | None = None


class ProgressBody(BaseModel):
    worker_id: str
    phase: str = ""
    summary: str
    blocker: str | None = None
    pr: str | None = None


class CardBody(BaseModel):
    worker_id: str
    card: dict


class SteerBody(BaseModel):
    fields: dict = Field(default_factory=dict)
    sender: str | None = None
    wake: bool = True
    message: str | None = None
    expected_status: str | None = None


class CardDraftBody(BaseModel):
    fields: dict = Field(default_factory=dict)


class SteerTakeBody(BaseModel):
    worker_id: str
    all_pending: bool = False


class AbandonBody(BaseModel):
    worker_id: str | None = None
    permitted: bool = False
    reason: str | None = None
    expected_status: str | None = None
    expected_generation: int | None = None
    expected_owner_session_id: str | None = None


class ResetBody(BaseModel):
    reason: str | None = None
    expected_status: str | None = None
    expected_generation: int | None = None
    expected_owner_session_id: str | None = None


class HoldBody(BaseModel):
    reason: str
    actor: str
    expected_status: str | None = None


class UnholdBody(BaseModel):
    actor: str | None = None
    expected_status: str | None = None


def _task_dict(task: Task) -> dict:
    return asdict(task)


def _task_with_spawn_dict(queue: TaskQueue, task: Task) -> dict:
    result = asdict(task)
    latest = queue.latest_reservation(task.id)
    if latest is not None:
        result["spawn_reservation"] = asdict(latest)
    return result


def _bulk_task_dict(task: Task) -> dict:
    result = asdict(task)
    result.pop("result")
    return result


def _event_task_dict(task: dict) -> dict:
    result = dict(task)
    result["has_result"] = result.pop("result", None) is not None or bool(
        result.get("has_result")
    )
    return result


def register_task_routes(
    app: FastAPI,
    queue: TaskQueue,
    bus: EventBus,
    *,
    control_token: str | None,
    resolve_owner_session_id: Callable[[str | None], str | None],
) -> None:
    def _require(task: Task | None) -> Task:
        if task is None:
            raise HTTPException(status_code=404, detail="no such task")
        return task

    def _emit(event_type: str, task: dict) -> None:
        event_task = _event_task_dict(task)
        bus.publish({"type": event_type, "task": event_task})
        telemetry.emit(telemetry.task_lifecycle_event(event_type, event_task))

    def _emit_producer_event(event_type: str, detail: dict[str, object]) -> None:
        bus.publish({"type": event_type, "producer_fence": detail})
        telemetry.emit(telemetry.producer_fence_event(event_type, detail))

    def _producer_rejection(exc: ProducerFenceError) -> None:
        _emit_producer_event(
            "task.create_rejected", exc.event(operation="create")
        )

    def _guard(op, event_type: str | None = None) -> dict:
        try:
            mutation = op()
            if isinstance(mutation, CompletionOutcome):
                event_type = mutation.event_type
                mutation = mutation.task
            result = _task_dict(mutation)
        except ResultTooLargeError as exc:
            raise HTTPException(status_code=413, detail=str(exc)) from exc
        except ResultValidationError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        if event_type in ("task.completed", "task.abandoned"):
            # A shared terminal-transition hook: every caller that reaches a
            # genuine (not idempotent-retry) completion or an abandon funnels
            # through this same _guard, whether over HTTP (the CLI's own
            # transport) or an in-process call from an MCP tool
            # (mcp_http.py's dispatch_complete/dispatch_abandon, which call
            # queue.complete_with_outcome/abandon directly, not through this
            # route) -- so this alone does not cover MCP; mcp_http.py wires
            # the same release call itself, right after its own equivalent
            # mutation succeeds.
            from . import handoff_claim_release

            handoff_claim_release.release_if_handoff(result)
        if event_type is not None:
            _emit(event_type, result)
        return result

    @app.get("/producer-scopes/status")
    def producer_scope_status(repo: str, source: str) -> dict:
        try:
            return asdict(queue.producer_scope_status(repo, source))
        except ProducerScopeValidationError as exc:
            raise HTTPException(
                status_code=400, detail=exc.detail(operation="status")
            ) from exc

    @app.post(
        "/producer-scopes/handoff",
        dependencies=[
            Depends(_make_control_auth(control_token, _emit_producer_event))
        ],
    )
    def producer_scope_handoff(body: ProducerScopeHandoffBody) -> dict:
        try:
            transition = queue.handoff_producer_scope(
                body.repo,
                body.source,
                producer_id=body.producer_id,
                expected_generation=body.expected_generation,
                required_label=body.required_label,
            )
        except ProducerScopeValidationError as exc:
            detail = exc.detail(operation="transition")
            _emit_producer_event(
                "producer_scope.transition_rejected",
                {key: value for key, value in detail.items() if key != "message"},
            )
            raise HTTPException(status_code=400, detail=detail) from exc
        except ProducerFenceError as exc:
            detail = exc.detail(operation="transition")
            _emit_producer_event(
                "producer_scope.transition_rejected",
                exc.event(operation="transition"),
            )
            raise HTTPException(status_code=409, detail=detail) from exc
        result = transition.as_dict()
        state = transition.state
        detail: dict[str, object] = {
            "repo": state.scope["repo"],
            "source": state.scope["source"],
            "from_generation": body.expected_generation,
            "to_generation": state.current_generation,
            "active_producer": state.active_producer,
            "replayed": transition.replayed,
        }
        if state.required_label is not None:
            detail["required_label"] = state.required_label
        _emit_producer_event("producer_scope.transitioned", detail)
        return result

    @app.post("/tasks")
    def create(body: CreateBody) -> dict:
        data = body.model_dump()
        proposed = data.pop("proposed")
        try:
            outcome = (
                queue.propose_outcome(**data)
                if proposed
                else queue.create_outcome(**data)
            )
            task = _task_dict(outcome.task)
        except ProducerScopeValidationError as exc:
            detail = exc.detail(operation="create")
            _emit_producer_event(
                "task.create_rejected",
                {key: value for key, value in detail.items() if key != "message"},
            )
            raise HTTPException(status_code=400, detail=detail) from exc
        except ProducerFenceError as exc:
            _producer_rejection(exc)
            status = 403 if exc.reason == "invalid_capability" else 409
            raise HTTPException(
                status_code=status, detail=exc.detail(operation="create")
            ) from exc
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc
        if outcome.event_type is not None:
            _emit(outcome.event_type, task)
        return task

    @app.get("/tasks")
    def list_tasks(
        repo: str | None = None,
        status: str | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        evaluator_ref: str | None = None,
        source: str | None = None,
        origin_ref: str | None = None,
        exclusive_key: str | None = None,
        q: str | None = None,
        sweep: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        if sweep:
            return [_bulk_task_dict(t) for t in queue.sweep(repo=repo, limit=limit)]
        if q is not None:
            return [_bulk_task_dict(t) for t in queue.find(q, repo=repo, limit=limit)]
        status_filter: str | list[str] | None = None
        if status is not None:
            parts = [s.strip() for s in status.split(",") if s.strip()]
            status_filter = parts[0] if len(parts) == 1 else parts
        tasks = queue.list(
            repo=repo,
            status=status_filter,
            target_machine=target_machine,
            target_repo=target_repo,
            label=label,
            evaluator_ref=evaluator_ref,
            source=source,
            origin_ref=origin_ref,
            exclusive_key=exclusive_key,
            limit=limit,
        )
        return [_bulk_task_dict(t) for t in tasks]

    @app.get("/tasks/mine")
    def mine(machine: str, worktree: str, repo: str | None = None) -> dict:
        result = queue.mine(machine, worktree, repo=repo)
        return {k: [_bulk_task_dict(t) for t in v] for k, v in result.items()}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        return _task_with_spawn_dict(queue, _require(queue.get(task_id)))

    @app.get("/tasks/{task_id}/result")
    def get_result(task_id: str) -> dict:
        task = _require(queue.get(task_id))
        return {
            "task_id": task.id,
            "ref": task.result_ref,
            "result": queue.read_result(task),
        }

    @app.get("/tasks/{task_id}/events")
    def get_events(task_id: str) -> list[dict]:
        _require(queue.get(task_id))
        return queue.events(task_id)

    @app.get("/tasks/{task_id}/wakes")
    def get_wakes(task_id: str) -> list[dict]:
        _require(queue.get(task_id))
        return [asdict(wake) for wake in queue.list_wakes(task_id)]

    @app.get("/tasks/{task_id}/progress-log")
    def get_progress_log(task_id: str) -> list[dict]:
        _require(queue.get(task_id))
        return queue.progress_log(task_id)

    @app.get("/tasks/{task_id}/attachments")
    def get_attachments(task_id: str) -> list[dict]:
        _require(queue.get(task_id))
        return [asdict(record) for record in queue.attachment_history(task_id)]

    @app.get("/sessions/{session_id}/tasks")
    def get_tasks_for_session(session_id: str) -> list[dict]:
        return [asdict(entry) for entry in queue.tasks_for_session(session_id)]

    @app.get("/tasks/{task_id}/payload")
    def get_payload(task_id: str) -> dict:
        task = _require(queue.get(task_id))
        content = queue.read_payload(task)
        return {
            "task_id": task.id,
            "ref": task.payload_ref,
            "inline": task.payload_inline is not None,
            "payload": content,
        }

    @app.post("/tasks/{task_id}/approve")
    def approve(task_id: str) -> dict:
        return _guard(lambda: queue.approve(task_id), "task.approved")

    @app.post("/claim")
    def claim(request: Request, body: ClaimBody) -> dict | None:
        gate: DrainGate = request.app.state.drain_gate
        if gate.draining:
            return None
        owner = body.worker_id
        if owner is None and body.machine and body.worktree:
            owner = worker_id_for(body.machine, body.worktree)
        if owner is None:
            raise HTTPException(
                status_code=422, detail="claim requires worker_id, or both machine and worktree"
            )
        if body.all_repos and body.repo:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "claim_scope_invalid",
                    "message": "claim accepts repo or all_repos=true, not both",
                },
            )
        if not body.all_repos and not body.repo:
            raise HTTPException(
                status_code=422,
                detail={
                    "code": "claim_scope_required",
                    "message": "claim requires repo or explicit all_repos=true",
                },
            )
        with gate.track_claim():
            outcome = queue.claim_outcome(
                owner,
                body.capabilities,
                repo=None if body.all_repos else body.repo,
                machine=body.machine,
                worktree=body.worktree,
                task_id=body.task_id,
                lease_seconds=body.lease_seconds,
                evaluation=body.evaluation,
            )
        for rejection in outcome.producer_rejections:
            _emit_producer_event("producer.claim_rejected", rejection)
        task = outcome.task
        if task is None:
            return None
        result = _task_dict(task)
        _emit("task.claimed", result)
        return result

    @app.post("/drain")
    async def drain(request: Request, body: DrainRequest | None = None) -> dict:
        gate: DrainGate = request.app.state.drain_gate
        opts = body or DrainRequest()
        timeout = max(0.0, float(opts.timeout))
        poll = max(0.05, float(opts.poll))
        gate.set_draining(True)
        clean = await asyncio.to_thread(gate.wait_for_claims, timeout=timeout, poll=poll)
        forced = bool(opts.force and not clean)
        drained = clean or forced
        return {
            "drained": drained,
            "clean": clean,
            "forced": forced,
            "busy_claims": gate.claims,
        }

    @app.post("/undrain")
    async def undrain(request: Request) -> dict:
        gate: DrainGate = request.app.state.drain_gate
        gate.set_draining(False)
        return {"draining": False}

    @app.post("/shutdown")
    def shutdown(request: Request) -> dict:
        server = getattr(request.app.state, "uvicorn_server", None)
        if server is not None:
            server.should_exit = True
        return {"shutdown": True}

    @app.post("/adopt-relay")
    def adopt_relay() -> dict:
        return {"adopted": False, "reason": "agent-dispatch has no relay"}

    @app.post("/tasks/{task_id}/start")
    def start(task_id: str, body: WorkerBody) -> dict:
        owner_session_id = body.owner_session_id or resolve_owner_session_id(body.worker_id)
        return _guard(
            lambda: queue.start(task_id, body.worker_id, owner_session_id=owner_session_id),
            "task.started",
        )

    @app.post("/tasks/{task_id}/yield")
    def yield_task(task_id: str, body: YieldBody) -> dict:
        return _guard(
            lambda: queue.yield_task(
                task_id,
                body.worker_id,
                note=body.note,
                exclude=body.exclude,
                release_spawn=body.release_spawn,
            ),
            "task.yielded",
        )

    @app.post("/tasks/{task_id}/suspend")
    def suspend(task_id: str, body: SuspendBody) -> dict:
        return _guard(
            lambda: queue.suspend(
                task_id,
                body.worker_id,
                reason=body.reason,
                expected_status=body.expected_status,
                expected_generation=body.expected_generation,
                expected_owner_session_id=body.expected_owner_session_id,
                reject_pending_steer=body.reject_pending_steer,
            ),
            "task.suspended",
        )

    @app.post("/tasks/{task_id}/resume")
    def resume(task_id: str, body: ResumeBody) -> dict:
        message = body.message or (
            f"Task {task_id} has been resumed. Continue toward its goal "
            "from the durable progress already recorded."
        )
        if body.adopt_session and body.adopt_owner_session_id:
            raise HTTPException(
                status_code=422,
                detail="resume accepts adopt_session or adopt_owner_session_id, not both",
            )
        adopt_owner_session_id = body.adopt_owner_session_id
        if body.adopt_session:
            adopt_owner_session_id = resolve_owner_session_id(body.worker_id)
            if adopt_owner_session_id is None:
                raise HTTPException(
                    status_code=409,
                    detail=(
                        f"cannot adopt task {task_id}: no current live session "
                        f"for owner {body.worker_id!r}"
                    ),
                )
        task = _guard(
            lambda: queue.resume(
                task_id,
                body.worker_id,
                wake_requested=body.wake,
                wake_message=message,
                adopt_owner_session_id=adopt_owner_session_id,
                reuse_session=body.reuse_session,
                expected_owner_session_id=body.expected_owner_session_id,
                expected_generation=body.expected_generation,
            ),
            "task.resumed",
        )
        return {
            **task,
            "resume_woken": None,
            "resume_wake_status": (
                task.get("wake_status") if body.wake else "not_requested"
            ),
        }

    @app.post("/tasks/{task_id}/release")
    def release(task_id: str, body: ReleaseBody) -> dict:
        return _guard(
            lambda: queue.release_suspended(
                task_id, body.worker_id, reason=body.reason
            ),
            "task.released",
        )

    @app.post("/tasks/{task_id}/complete")
    def complete(task_id: str, body: CompleteBody) -> dict:
        return _guard(
            lambda: queue.complete_with_outcome(
                task_id,
                body.worker_id,
                result_ref=body.result_ref,
                result=body.result,
                expected_status=body.expected_status,
                expected_owner_session_id=body.expected_owner_session_id,
                expected_generation=body.expected_generation,
            )
        )

    @app.post("/tasks/{task_id}/abandon")
    def abandon(task_id: str, body: AbandonBody) -> dict:
        return _guard(
            lambda: queue.abandon_with_outcome(
                task_id,
                worker_id=body.worker_id,
                permitted=body.permitted,
                reason=body.reason,
                expected_status=body.expected_status,
                expected_generation=body.expected_generation,
                expected_owner_session_id=body.expected_owner_session_id,
            )
        )

    @app.post("/tasks/{task_id}/hold")
    def hold(task_id: str, body: HoldBody) -> dict:
        return _guard(
            lambda: queue.set_hold(
                task_id,
                reason=body.reason,
                actor=body.actor,
                expected_status=body.expected_status,
            ),
            "task.held",
        )

    @app.post("/tasks/{task_id}/unhold")
    def unhold(task_id: str, body: UnholdBody) -> dict:
        return _guard(
            lambda: queue.clear_hold(
                task_id,
                actor=body.actor,
                expected_status=body.expected_status,
            ),
            "task.unheld",
        )

    @app.post("/tasks/{task_id}/reset")
    def reset(task_id: str, body: ResetBody) -> dict:
        return _guard(
            lambda: queue.reset(
                task_id,
                reason=body.reason,
                expected_status=body.expected_status,
                expected_generation=body.expected_generation,
                expected_owner_session_id=body.expected_owner_session_id,
            ),
            "task.reset",
        )

    @app.post("/tasks/{task_id}/heartbeat")
    def heartbeat(task_id: str, body: WorkerBody) -> dict:
        return _guard(lambda: queue.heartbeat(task_id, body.worker_id))

    @app.post("/tasks/{task_id}/activity")
    def activity(task_id: str, body: ActivityBody) -> dict:
        return _guard(
            lambda: queue.set_activity(
                task_id, body.activity, reservation_key=body.reservation_key
            )
        )

    @app.post("/tasks/{task_id}/owner-session")
    def bind_owner_session(task_id: str, body: OwnerSessionBody) -> dict:
        return _guard(
            lambda: queue.bind_owner_session(
                task_id,
                body.worker_id,
                body.owner_session_id,
                expected_generation=body.expected_generation,
            ),
            "task.owner_session_bound",
        )

    @app.post("/tasks/{task_id}/progress")
    def progress(task_id: str, body: ProgressBody) -> dict:
        return _guard(
            lambda: queue.record_progress(
                task_id,
                body.worker_id,
                phase=body.phase,
                summary=body.summary,
                blocker=body.blocker,
                pr=body.pr,
            ),
            "task.progress",
        )

    @app.post("/tasks/{task_id}/detach")
    def detach(task_id: str) -> dict:
        return _guard(lambda: queue.detach(task_id), "task.detached")

    @app.post("/tasks/{task_id}/card")
    def set_card(task_id: str, body: CardBody) -> dict:
        return _guard(
            lambda: queue.set_card(task_id, body.worker_id, card=body.card),
            "task.card",
        )

    @app.post("/tasks/{task_id}/steer")
    def steer(task_id: str, body: SteerBody) -> dict:
        message = body.message or (
            f"The operator answered your card on task {task_id}. Resume, run "
            f"`agent-dispatch steer take {task_id} --all` to read every pending "
            "answer, and "
            "continue toward your goal."
        )
        task = _guard(
            lambda: queue.submit_steer(
                task_id,
                fields=body.fields,
                sender=body.sender,
                wake_requested=body.wake,
                wake_message=message,
                expected_status=body.expected_status,
            ),
            "task.steer",
        )
        owner = task.get("owner")
        return {
            **task,
            "steer_woken": None,
            "steer_wake_status": (
                task.get("wake_status")
                if body.wake and owner
                else "no_owner" if body.wake else "not_requested"
            ),
        }

    @app.post("/tasks/{task_id}/card-draft")
    def save_card_draft(task_id: str, body: CardDraftBody) -> dict:
        return _guard(
            lambda: queue.save_card_draft(task_id, fields=body.fields),
            "task.card_draft",
        )

    @app.delete("/tasks/{task_id}/card-draft")
    def clear_card_draft(task_id: str) -> dict:
        return _guard(
            lambda: queue.clear_card_draft(task_id),
            "task.card_draft_cleared",
        )

    @app.post("/tasks/{task_id}/steer/take")
    def steer_take(task_id: str, body: SteerTakeBody) -> dict:
        try:
            steer = queue.take_steer(
                task_id, body.worker_id, all_pending=body.all_pending
            )
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        key = "steers" if body.all_pending else "steer"
        return {"task_id": task_id, key: steer}

    @app.get("/tasks/{task_id}/steer-log")
    def get_steer_log(task_id: str) -> list[dict]:
        _require(queue.get(task_id))
        return queue.steer_log(task_id)

    @app.post("/recover")
    def recover() -> dict:
        counts = queue.reconcile_liveness()
        return {"recovered": counts["requeued"], **counts}
