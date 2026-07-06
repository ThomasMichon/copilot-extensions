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

from . import __version__
from .events import EventBus, sse_format
from .queue import Task, TaskError, TaskQueue, worker_id_for

log = logging.getLogger("agent-dispatch.coordinator")


async def _sweep_loop(queue: TaskQueue, interval: float, bus: EventBus) -> None:
    """Periodically recover expired leases so a crashed worker's task resurfaces.

    Runs the (synchronous) recovery sweep off the event loop via a worker thread.
    Cancelled cleanly on shutdown.
    """
    while True:
        await asyncio.sleep(interval)
        try:
            recovered = await asyncio.to_thread(queue.recover_expired_leases)
        except Exception:  # pragma: no cover -- never let the loop die on a blip
            log.exception("lease-recovery sweep failed")
            continue
        if recovered:
            log.info("lease-recovery sweep requeued %d expired task(s)", recovered)
            bus.publish({"type": "task.swept", "recovered": recovered})


class CreateBody(BaseModel):
    title: str
    repo: str | None = None
    prompt: str = ""
    proposed: bool = False
    requires: list[str] = Field(default_factory=list)
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
    not_before: float = 0.0


class ClaimBody(BaseModel):
    worker_id: str | None = None
    repo: str | None = None
    machine: str | None = None
    worktree: str | None = None
    capabilities: list[str] = Field(default_factory=list)
    task_id: str | None = None
    lease_seconds: int | None = None


class WorkerBody(BaseModel):
    worker_id: str


class YieldBody(BaseModel):
    worker_id: str
    note: str | None = None


class CompleteBody(BaseModel):
    worker_id: str
    result_ref: str | None = None


class AbandonBody(BaseModel):
    worker_id: str | None = None
    permitted: bool = False
    reason: str | None = None


def _task_dict(task: Task) -> dict:
    return asdict(task)


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

    mcp_app = None
    if enable_mcp:
        try:
            from .mcp_http import bearer_guard_middleware, build_coordinator_mcp

            mcp_app = build_coordinator_mcp(queue, bus).streamable_http_app()
            if token:
                mcp_app.add_middleware(bearer_guard_middleware(token))
        except ImportError:
            log.warning("mcp extra not installed; coordinator /mcp endpoint disabled")
            mcp_app = None

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        sweeper = (
            asyncio.create_task(_sweep_loop(queue, sweep_interval, bus))
            if sweep_interval and sweep_interval > 0
            else None
        )
        async with contextlib.AsyncExitStack() as stack:
            if mcp_app is not None:
                # Run the MCP session manager alongside the coordinator lifespan.
                await stack.enter_async_context(mcp_app.router.lifespan_context(_app))
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

    def _require(task: Task | None) -> Task:
        if task is None:
            raise HTTPException(status_code=404, detail="no such task")
        return task

    def _emit(event_type: str, task: dict) -> None:
        bus.publish({"type": event_type, "task": task})

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
    def health() -> dict:
        return {"status": "ok", "version": __version__, "subscribers": bus.subscriber_count}

    @app.get("/events")
    async def events_stream() -> StreamingResponse:
        async def gen():
            async for event in bus.subscribe():
                yield sse_format(event)

        return StreamingResponse(gen(), media_type="text/event-stream")

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
        )
        if task is None:
            return None
        result = _task_dict(task)
        _emit("task.claimed", result)
        return result

    @app.post("/tasks/{task_id}/start")
    def start(task_id: str, body: WorkerBody) -> dict:
        return _guard(lambda: queue.start(task_id, body.worker_id), "task.started")

    @app.post("/tasks/{task_id}/yield")
    def yield_task(task_id: str, body: YieldBody) -> dict:
        return _guard(
            lambda: queue.yield_task(task_id, body.worker_id, note=body.note), "task.yielded"
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

    @app.post("/tasks/{task_id}/detach")
    def detach(task_id: str) -> dict:
        return _guard(lambda: queue.detach(task_id), "task.detached")

    @app.post("/recover")
    def recover() -> dict:
        return {"recovered": queue.recover_expired_leases()}

    if mcp_app is not None:
        # Mounted last so the coordinator's own routes take precedence.
        app.mount("/mcp", mcp_app)

    return app
