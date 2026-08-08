"""FastAPI coordinator -- the single-writer HTTP front for the task queue.

The coordinator is the *only* writer to the SQLite queue; every other
participant (agents, producers, the CLI) is an HTTP client. This keeps the
atomic-claim guarantees of :class:`~agent_dispatch.queue.TaskQueue` intact with
no cross-host locking. SSE event emission and agent-bridge integration land in a
later slice; this module is the task CRUD + claim/lease API.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import __version__, telemetry
from .events import EventBus, sse_format
from .queue import SpawnReservation, Task, TaskError, TaskQueue, worker_id_for
from .satellites import (
    ROLE_SATELLITE,
    FleetDirectory,
    UnknownInstance,
)

log = logging.getLogger("agent-dispatch.coordinator")


def _resolve_owner_session_id(worker_id: str | None) -> str | None:
    """Best-effort: resolve a worker's (``machine/worktree``) current live-session
    id, captured on ``start`` as the task's owner identity for liveness GC.

    Shells the same agent-bridge live-session resolver `tracking` uses. Any
    failure (no bridge, unreachable, no session yet) returns ``None`` -- the task
    is then simply not GC-attributable until a later capture, never wrongly
    requeued (an owner without a captured identity reads ``unknown``).
    """
    if not worker_id or "/" not in worker_id:
        return None
    from . import tracking

    machine, _sep, worktree = worker_id.partition("/")
    if not worktree:
        return None
    local = tracking.remote_dispatch.local_machine()
    is_remote = bool(machine) and bool(local) and machine != local
    session = tracking.resolve_live_session(
        worktree, machine=machine if is_remote else None
    )
    if not session:
        return None
    return session.get("session_id") or session.get("id")


async def _gc_loop(queue: TaskQueue, interval: float, bus: EventBus) -> None:
    """Periodically garbage-collect held tasks by **worker liveness**.

    Replaces the old lease-expiry sweep: a held task is requeued only when its
    owner worktree is *confirmed gone* (not because a wall-clock lease elapsed),
    so long-running live work is never disturbed and a bridge blip (verdict
    ``unknown``) leaves a task alone. Runs the (synchronous, subprocess-shelling)
    reconcile off the event loop via a worker thread. Cancelled cleanly on
    shutdown.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            counts = await asyncio.to_thread(queue.reconcile_liveness)
        except Exception:  # pragma: no cover -- never let the loop die on a blip
            log.exception("liveness GC pass failed")
            continue
        requeued = counts.get("requeued", 0)
        if requeued:
            log.info(
                "liveness GC requeued %d task(s) with a gone owner (checked %d)",
                requeued,
                counts.get("checked", 0),
            )
            bus.publish({"type": "task.reconciled", "requeued": requeued, **counts})


class CreateBody(BaseModel):
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
    source: str | None = None
    origin_ref: str | None = None
    dedup_key: str | None = None
    goal: str | None = None
    done_criteria: str | None = None
    not_before: float = 0.0
    claim_as: str | None = None


class ClaimBody(BaseModel):
    worker_id: str | None = None
    repo: str | None = None
    machine: str | None = None
    worktree: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    task_id: str | None = None
    lease_seconds: int | None = None
    evaluation: bool = False


class WorkerBody(BaseModel):
    worker_id: str
    #: Optional: the worktree's current live-session id, captured on ``start`` as
    #: the task's owner identity (for liveness GC). When omitted the coordinator
    #: resolves it best-effort from the owner worktree.
    owner_session_id: str | None = None


class YieldBody(BaseModel):
    worker_id: str
    note: str | None = None
    exclude: str | None = None


class CompleteBody(BaseModel):
    worker_id: str
    result_ref: str | None = None


class ProgressBody(BaseModel):
    worker_id: str
    phase: str = ""
    summary: str
    blocker: str | None = None
    pr: str | None = None


class AbandonBody(BaseModel):
    worker_id: str | None = None
    permitted: bool = False
    reason: str | None = None


class ReserveSpawnBody(BaseModel):
    task_id: str
    reserved_by: str | None = None


class RecordSpawnBody(BaseModel):
    session_handle: str | None = None
    worktree: str | None = None


class ReservationDetailBody(BaseModel):
    detail: str | None = None


class ScheduleLeaseBody(BaseModel):
    holder: str
    holder_session: str | None = None
    ttl: float | None = None


class ReleaseLeaseBody(BaseModel):
    holder: str
    force: bool = False


class RegistrationBody(BaseModel):
    kind: str
    spec: dict
    id: str | None = None
    machine: str | None = None
    env: str = "default"


class RegistrationStatusBody(BaseModel):
    status: str


class SatelliteRegisterBody(BaseModel):
    machine: str
    worktrees: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    gate_state: str = "open"
    agent_versions: dict[str, str] = Field(default_factory=dict)
    status: dict = Field(default_factory=dict)


class SatelliteHeartbeatBody(BaseModel):
    status: dict | None = None
    worktrees: list[str] | None = None
    gate_state: str | None = None


class DirectoryRegisterBody(BaseModel):
    instance: str
    role: str = "peer"
    epoch: int = 0
    machine: str | None = None
    worktrees: list[str] = Field(default_factory=list)
    capabilities: list[str] = Field(default_factory=list)
    gate_state: str = "open"
    agent_versions: dict[str, str] = Field(default_factory=dict)
    status: dict = Field(default_factory=dict)


class DirectoryHeartbeatBody(BaseModel):
    status: dict | None = None
    worktrees: list[str] | None = None
    gate_state: str | None = None
    role: str | None = None
    epoch: int | None = None


def _task_dict(task: Task) -> dict:
    return asdict(task)


def _reservation_dict(res: SpawnReservation) -> dict:
    return asdict(res)


def _make_auth(token: str | None):
    bearer = HTTPBearer(auto_error=False)

    def check(creds: HTTPAuthorizationCredentials | None = Depends(bearer)) -> None:  # noqa: B008
        if token is None:
            return
        if creds is None or creds.credentials != token:
            raise HTTPException(status_code=401, detail="invalid or missing bearer token")

    return check


def create_app(
    queue: TaskQueue,
    *,
    token: str | None = None,
    sweep_interval: float = 0.0,
    enable_mcp: bool = True,
) -> FastAPI:
    """Build the coordinator app over an existing :class:`TaskQueue`.

    When ``sweep_interval > 0`` the coordinator runs a background lease-recovery
    sweep every ``sweep_interval`` seconds so a crashed worker's held task
    automatically returns to ``queued`` without a manual ``recover`` call.

    When ``enable_mcp`` is set and the ``mcp`` extra is installed, a
    coordinator-hosted MCP endpoint is mounted at ``/mcp`` (identity via
    ``X-Agent-Machine``/``X-Agent-Worktree`` headers or explicit tool args).
    """
    bus = EventBus()
    directory = FleetDirectory()

    coordinator_mcp = None
    mcp_app = None
    if enable_mcp:
        try:
            from .mcp_http import bearer_guard_middleware, build_coordinator_mcp

            coordinator_mcp = build_coordinator_mcp(queue, bus)
            # mcp 2.0: transport options moved off the constructor onto the app
            # factory. streamable_http_path="/" so mounting at "/mcp" yields the
            # endpoint at "/mcp" (not "/mcp/mcp").
            mcp_app = coordinator_mcp.streamable_http_app(
                stateless_http=True, streamable_http_path="/"
            )
            if token:
                mcp_app.add_middleware(bearer_guard_middleware(token))
        except ImportError:
            log.warning("mcp extra not installed; coordinator /mcp endpoint disabled")
            coordinator_mcp = None
            mcp_app = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        sweeper = (
            asyncio.create_task(_gc_loop(queue, sweep_interval, bus))
            if sweep_interval and sweep_interval > 0
            else None
        )
        async with contextlib.AsyncExitStack() as stack:
            if coordinator_mcp is not None:
                # mcp 2.0: a mounted sub-app's own lifespan doesn't run, so drive
                # the streamable-HTTP session manager from the host lifespan.
                await stack.enter_async_context(coordinator_mcp.session_manager.run())
            try:
                yield
            finally:
                if sweeper is not None:
                    sweeper.cancel()
                    try:
                        await sweeper
                    except asyncio.CancelledError:
                        pass

    app = FastAPI(
        title="agent-dispatch",
        version=__version__,
        dependencies=[Depends(_make_auth(token))],
        lifespan=lifespan,
    )
    app.state.bus = bus
    app.state.directory = directory
    # Back-compat alias for the pre-generalization attribute name.
    app.state.satellites = directory

    def _require(task: Task | None) -> Task:
        if task is None:
            raise HTTPException(status_code=404, detail="no such task")
        return task

    def _emit(event_type: str, task: dict) -> None:
        bus.publish({"type": event_type, "task": task})
        # Generic telemetry seam (no-op unless a consumer registered a sink).
        telemetry.emit(telemetry.task_lifecycle_event(event_type, task))

    def _guard(op, event_type: str | None = None) -> dict:
        """Run a queue mutation (TaskError -> 409 / missing -> 404), then emit."""
        try:
            result = _task_dict(op())
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such task") else 409
            raise HTTPException(status_code=status, detail=msg) from exc
        if event_type is not None:
            _emit(event_type, result)
        return result

    @app.get("/health")
    def health(repo: str | None = None) -> dict:
        return {
            "status": "ok",
            "version": __version__,
            "subscribers": bus.subscriber_count,
            "backlog": queue.backlog_health(repo=repo),
        }

    @app.get("/events")
    async def events_stream() -> StreamingResponse:
        async def gen():
            async for event in bus.subscribe():
                yield sse_format(event)

        return StreamingResponse(gen(), media_type="text/event-stream")

    # -- fleet directory (awareness plane) + satellite façade ----------------
    # Every federating instance registers here so the fleet is enumerable from
    # any seat (awareness plane); a coordinator advertises itself so peers
    # *discover* it (claim plane) rather than electing one. A satellite is just
    # a directory entry with role="satellite" -- an outbound-only field machine
    # the facility never dials into.
    @app.post("/directory/register")
    def directory_register(body: DirectoryRegisterBody) -> dict:
        return directory.register(
            body.instance,
            role=body.role,
            epoch=body.epoch,
            machine=body.machine,
            worktrees=body.worktrees,
            capabilities=body.capabilities,
            gate_state=body.gate_state,
            agent_versions=body.agent_versions,
            status=body.status,
        )

    @app.post("/directory/{instance}/heartbeat")
    def directory_heartbeat(instance: str, body: DirectoryHeartbeatBody) -> dict:
        try:
            return directory.heartbeat(
                instance,
                status=body.status,
                worktrees=body.worktrees,
                gate_state=body.gate_state,
                role=body.role,
                epoch=body.epoch,
            )
        except UnknownInstance as exc:
            raise HTTPException(
                status_code=404, detail="unknown instance"
            ) from exc

    @app.delete("/directory/{instance}")
    def directory_deregister(instance: str) -> dict:
        return {"deregistered": directory.deregister(instance)}

    @app.get("/directory")
    def directory_list(role: str | None = None) -> list[dict]:
        return directory.discover_peers(role=role)

    @app.get("/directory/coordinator")
    def directory_coordinator() -> dict | None:
        return directory.discover_coordinator()

    # Satellite façade: same store, role pinned to "satellite".
    @app.post("/satellites/register")
    def satellite_register(body: SatelliteRegisterBody) -> dict:
        return directory.register(
            body.machine,
            role=ROLE_SATELLITE,
            worktrees=body.worktrees,
            capabilities=body.capabilities,
            gate_state=body.gate_state,
            agent_versions=body.agent_versions,
            status=body.status,
        )

    @app.post("/satellites/{machine}/heartbeat")
    def satellite_heartbeat(machine: str, body: SatelliteHeartbeatBody) -> dict:
        try:
            return directory.heartbeat(
                machine,
                status=body.status,
                worktrees=body.worktrees,
                gate_state=body.gate_state,
            )
        except UnknownInstance as exc:
            # 404 tells the satellite client to re-register rather than resurrect
            # a reaped entry.
            raise HTTPException(status_code=404, detail="unknown satellite") from exc

    @app.delete("/satellites/{machine}")
    def satellite_deregister(machine: str) -> dict:
        return {"deregistered": directory.deregister(machine)}

    @app.get("/satellites")
    def satellite_list() -> list[dict]:
        return directory.discover_peers(role=ROLE_SATELLITE)

    @app.post("/tasks")
    def create(body: CreateBody) -> dict:
        data = body.model_dump()
        proposed = data.pop("proposed")
        task = _task_dict(queue.propose(**data) if proposed else queue.create(**data))
        _emit("task.proposed" if proposed else "task.created", task)
        return task

    @app.get("/tasks")
    def list_tasks(
        repo: str | None = None,
        status: str | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        q: str | None = None,
        sweep: bool = False,
        limit: int = 200,
    ) -> list[dict]:
        if sweep:
            return [_task_dict(t) for t in queue.sweep(repo=repo, limit=limit)]
        if q is not None:
            return [_task_dict(t) for t in queue.find(q, repo=repo, limit=limit)]
        # ``status`` may be a single state or a comma-separated set (multi-state
        # browse), e.g. ``?status=queued,started``.
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
            limit=limit,
        )
        return [_task_dict(t) for t in tasks]

    @app.get("/tasks/mine")
    def mine(machine: str, worktree: str, repo: str | None = None) -> dict:
        result = queue.mine(machine, worktree, repo=repo)
        return {k: [_task_dict(t) for t in v] for k, v in result.items()}

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        return _task_dict(_require(queue.get(task_id)))

    @app.get("/tasks/{task_id}/events")
    def get_events(task_id: str) -> list[dict]:
        _require(queue.get(task_id))
        return queue.events(task_id)

    @app.get("/tasks/{task_id}/progress-log")
    def get_progress_log(task_id: str) -> list[dict]:
        """The accumulated append-only progress log (oldest first)."""
        _require(queue.get(task_id))
        return queue.progress_log(task_id)

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
    def claim(body: ClaimBody) -> dict | None:
        owner = body.worker_id
        if owner is None and body.machine and body.worktree:
            owner = worker_id_for(body.machine, body.worktree)
        if owner is None:
            raise HTTPException(
                status_code=422, detail="claim requires worker_id, or both machine and worktree"
            )
        task = queue.claim_one(
            owner,
            body.capabilities,
            repo=body.repo,
            machine=body.machine,
            worktree=body.worktree,
            task_id=body.task_id,
            lease_seconds=body.lease_seconds,
            evaluation=body.evaluation,
        )
        if task is None:
            return None
        result = _task_dict(task)
        _emit("task.claimed", result)
        return result

    @app.post("/tasks/{task_id}/start")
    def start(task_id: str, body: WorkerBody) -> dict:
        owner_session_id = body.owner_session_id or _resolve_owner_session_id(body.worker_id)
        return _guard(
            lambda: queue.start(task_id, body.worker_id, owner_session_id=owner_session_id),
            "task.started",
        )

    @app.post("/tasks/{task_id}/yield")
    def yield_task(task_id: str, body: YieldBody) -> dict:
        return _guard(
            lambda: queue.yield_task(
                task_id, body.worker_id, note=body.note, exclude=body.exclude
            ),
            "task.yielded",
        )

    @app.post("/tasks/{task_id}/complete")
    def complete(task_id: str, body: CompleteBody) -> dict:
        return _guard(
            lambda: queue.complete(task_id, body.worker_id, result_ref=body.result_ref),
            "task.completed",
        )

    @app.post("/tasks/{task_id}/abandon")
    def abandon(task_id: str, body: AbandonBody) -> dict:
        return _guard(
            lambda: queue.abandon(
                task_id, worker_id=body.worker_id, permitted=body.permitted, reason=body.reason
            ),
            "task.abandoned",
        )

    @app.post("/tasks/{task_id}/heartbeat")
    def heartbeat(task_id: str, body: WorkerBody) -> dict:
        return _guard(lambda: queue.heartbeat(task_id, body.worker_id))

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

    @app.post("/recover")
    def recover() -> dict:
        """Force a liveness GC pass now (requeue tasks whose owner is gone)."""
        counts = queue.reconcile_liveness()
        # Back-compat: keep the old ``recovered`` key alongside the richer counts.
        return {"recovered": counts["requeued"], **counts}

    # -- spawn reservations --------------------------------------------------

    @app.post("/spawn-reservations")
    def reserve_spawn(body: ReserveSpawnBody) -> dict:
        """Atomically reserve the right to spawn an embody worker for a task.

        Returns ``{"reserved": bool, "reservation": {...}}``. ``reserved`` is
        ``False`` when an active reservation already exists (the caller must NOT
        spawn); ``True`` when this caller now owns a fresh (task, attempt) spawn.
        """
        _require(queue.get(body.task_id))
        reservation, reserved = queue.reserve_spawn(
            body.task_id, reserved_by=body.reserved_by
        )
        result = _reservation_dict(reservation)
        if reserved:
            bus.publish({"type": "spawn.reserved", "reservation": result})
        return {"reserved": reserved, "reservation": result}

    def _reservation_guard(op) -> dict:
        try:
            return _reservation_dict(op())
        except TaskError as exc:
            msg = str(exc)
            status = 404 if msg.startswith("no such reservation") else 409
            raise HTTPException(status_code=status, detail=msg) from exc

    @app.post("/spawn-reservations/{key}/spawned")
    def record_spawn(key: str, body: RecordSpawnBody) -> dict:
        result = _reservation_guard(
            lambda: queue.record_spawn(
                key, session_handle=body.session_handle, worktree=body.worktree
            )
        )
        bus.publish({"type": "spawn.spawned", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/fail")
    def fail_spawn(key: str, body: ReservationDetailBody) -> dict:
        result = _reservation_guard(lambda: queue.fail_spawn(key, detail=body.detail))
        bus.publish({"type": "spawn.failed", "reservation": result})
        return result

    @app.post("/spawn-reservations/{key}/settle")
    def settle_spawn(key: str, body: ReservationDetailBody) -> dict:
        result = _reservation_guard(lambda: queue.settle_spawn(key, detail=body.detail))
        bus.publish({"type": "spawn.settled", "reservation": result})
        return result

    @app.get("/spawn-reservations")
    def list_reservations(
        task_id: str | None = None, state: str | None = None, limit: int = 200
    ) -> list[dict]:
        states = (
            [s.strip() for s in state.split(",") if s.strip()] if state else None
        )
        return [
            _reservation_dict(r)
            for r in queue.list_reservations(task_id=task_id, state=states, limit=limit)
        ]

    @app.get("/spawn-reservations/{key}")
    def get_reservation(key: str) -> dict:
        reservation = queue.get_reservation(key)
        if reservation is None:
            raise HTTPException(status_code=404, detail="no such reservation")
        return _reservation_dict(reservation)

    # -- schedule registry ---------------------------------------------------

    @app.post("/schedules")
    def register_schedule(entry: dict) -> dict:
        """Register (or upsert) a recurring schedule. 400 on a malformed entry."""
        try:
            return asdict(queue.register_schedule(entry))
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/schedules")
    def list_schedules(include_paused: bool = True) -> list[dict]:
        return [asdict(r) for r in queue.list_schedules(include_paused=include_paused)]

    @app.get("/schedules/{sid}")
    def get_schedule(sid: str) -> dict:
        rec = queue.get_schedule(sid)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such schedule")
        return asdict(rec)

    @app.delete("/schedules/{sid}")
    def remove_schedule(sid: str) -> dict:
        if not queue.remove_schedule(sid):
            raise HTTPException(status_code=404, detail="no such schedule")
        return {"removed": True, "id": sid}

    @app.post("/schedules/{sid}/pause")
    def pause_schedule(sid: str) -> dict:
        try:
            return asdict(queue.set_schedule_paused(sid, True))
        except TaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    @app.post("/schedules/{sid}/resume")
    def resume_schedule(sid: str) -> dict:
        try:
            return asdict(queue.set_schedule_paused(sid, False))
        except TaskError as exc:
            raise HTTPException(status_code=404, detail=str(exc)) from exc

    # -- supervisor registrations --------------------------------------------

    @app.post("/registrations")
    def register_registration(body: RegistrationBody) -> dict:
        """Register (or upsert) a supervision unit; return its handle. 400 on a
        malformed kind/spec."""
        try:
            return asdict(
                queue.register_registration(
                    body.kind,
                    body.spec,
                    reg_id=body.id,
                    machine=body.machine,
                    env=body.env,
                )
            )
        except TaskError as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.get("/registrations")
    def list_registrations(
        kind: str | None = None,
        machine: str | None = None,
        env: str | None = None,
        include_paused: bool = True,
    ) -> list[dict]:
        return [
            asdict(r)
            for r in queue.list_registrations(
                kind=kind, machine=machine, env=env, include_paused=include_paused
            )
        ]

    @app.get("/registrations/{rid}")
    def get_registration(rid: str) -> dict:
        rec = queue.get_registration(rid)
        if rec is None:
            raise HTTPException(status_code=404, detail="no such registration")
        return asdict(rec)

    @app.delete("/registrations/{rid}")
    def remove_registration(rid: str) -> dict:
        if not queue.remove_registration(rid):
            raise HTTPException(status_code=404, detail="no such registration")
        return {"removed": True, "id": rid}

    @app.post("/registrations/{rid}/status")
    def set_registration_status(rid: str, body: RegistrationStatusBody) -> dict:
        try:
            return asdict(queue.set_registration_status(rid, body.status))
        except TaskError as exc:
            code = 404 if "no such registration" in str(exc) else 400
            raise HTTPException(status_code=code, detail=str(exc)) from exc

    # -- schedule job-leases -------------------------------------------------

    @app.post("/schedule-leases/{scope}/acquire")
    def acquire_lease(scope: str, body: ScheduleLeaseBody) -> dict:
        lease, granted = queue.acquire_schedule_lease(
            scope, body.holder, holder_session=body.holder_session, ttl=body.ttl
        )
        return {"granted": granted, "lease": asdict(lease)}

    @app.post("/schedule-leases/{scope}/release")
    def release_lease(scope: str, body: ReleaseLeaseBody) -> dict:
        try:
            released = queue.release_schedule_lease(scope, body.holder, force=body.force)
        except TaskError as exc:
            raise HTTPException(status_code=409, detail=str(exc)) from exc
        return {"released": released, "scope": scope}

    @app.get("/schedule-leases")
    def list_leases() -> list[dict]:
        return [asdict(lease) for lease in queue.list_schedule_leases()]

    @app.get("/schedule-leases/{scope}")
    def get_lease(scope: str) -> dict | None:
        lease = queue.get_schedule_lease(scope)
        return asdict(lease) if lease else None

    if mcp_app is not None:
        # Mounted last so the coordinator's own routes take precedence.
        app.mount("/mcp", mcp_app)

    return app
