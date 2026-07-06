"""FastAPI coordinator -- the single-writer HTTP front for the task queue.

The coordinator is the *only* writer to the SQLite queue; every other
participant (agents, producers, the CLI) is an HTTP client. This keeps the
atomic-claim guarantees of :class:`~agent_dispatch.queue.TaskQueue` intact with
no cross-host locking. SSE event emission and agent-bridge integration land in a
later slice; this module is the task CRUD + claim/lease API.
"""

from __future__ import annotations

import asyncio
from contextlib import asynccontextmanager
from dataclasses import asdict

from fastapi import Depends, FastAPI, HTTPException
from fastapi.responses import StreamingResponse
from fastapi.security import HTTPAuthorizationCredentials, HTTPBearer
from pydantic import BaseModel, Field

from . import __version__
from .events import EventBus, sse_format
from .queue import Task, TaskError, TaskQueue


class CreateBody(BaseModel):
    title: str
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
    worker_id: str
    capabilities: list[str] = Field(default_factory=list)
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


def create_app(queue: TaskQueue, *, token: str | None = None) -> FastAPI:
    """Build the coordinator app over an existing :class:`TaskQueue`."""
    bus = EventBus()

    @asynccontextmanager
    async def lifespan(_app: FastAPI):
        bus.bind_loop(asyncio.get_running_loop())
        yield

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
        status: str | None = None,
        target_machine: str | None = None,
        target_repo: str | None = None,
        label: str | None = None,
        q: str | None = None,
        limit: int = 200,
    ) -> list[dict]:
        if q is not None:
            return [_task_dict(t) for t in queue.find(q, limit=limit)]
        tasks = queue.list(
            status=status,
            target_machine=target_machine,
            target_repo=target_repo,
            label=label,
            limit=limit,
        )
        return [_task_dict(t) for t in tasks]

    @app.get("/tasks/{task_id}")
    def get_task(task_id: str) -> dict:
        return _task_dict(_require(queue.get(task_id)))

    @app.get("/tasks/{task_id}/events")
    def get_events(task_id: str) -> list[dict]:
        _require(queue.get(task_id))
        return queue.events(task_id)

    @app.post("/tasks/{task_id}/approve")
    def approve(task_id: str) -> dict:
        return _guard(lambda: queue.approve(task_id), "task.approved")

    @app.post("/claim")
    def claim(body: ClaimBody) -> dict | None:
        task = queue.claim_one(
            body.worker_id, body.capabilities, lease_seconds=body.lease_seconds
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

    return app
