"""Coordinator status and event-stream routes."""

from __future__ import annotations

from typing import Any

from fastapi import FastAPI, Request
from fastapi.responses import StreamingResponse

from . import __version__
from .coordinator_loops import DrainGate, LoopHealth
from .events import EventBus, sse_format
from .queue import TaskQueue


def _slot_descriptor(app_state: Any) -> dict:
    """Render this coordinator's own slot-ownership view for ``/health``."""
    import os as _os

    from zdd import routing

    from .config import routing_dir

    my_pid = _os.getpid()
    try:
        table = routing.read_table(routing_dir()) or {}
    except Exception:
        table = {}
    active = table.get("active") if isinstance(table, dict) else None
    previous = table.get("previous") if isinstance(table, dict) else None
    active_pid = active.get("pid") if isinstance(active, dict) else None
    if (
        not isinstance(active_pid, int)
        or isinstance(active_pid, bool)
        or active_pid <= 0
    ):
        role = "unknown"
    elif active_pid == my_pid:
        role = "active"
    else:
        role = "passive"
    return {
        "pid": my_pid,
        "role": role,
        "active": active if isinstance(active, dict) else None,
        "previous": previous if isinstance(previous, dict) else None,
        "self_retire": dict(
            getattr(app_state, "self_retire_status", None)
            or {"enabled": False, "armed": False, "generation": None,
                "superseded": False, "confirms": 0}
        ),
        "abandoned_passive_reap": dict(
            getattr(app_state, "abandoned_passive_reap_status", None)
            or {"enabled": False, "armed": False, "last_outcome": None}
        ),
    }


def register_status_routes(
    app: FastAPI,
    queue: TaskQueue,
    bus: EventBus,
) -> None:
    @app.get("/health")
    def health(request: Request, repo: str | None = None) -> dict:
        gate: DrainGate = request.app.state.drain_gate
        loop_health: dict[str, LoopHealth] = getattr(
            request.app.state, "loop_health", {}
        )
        return {
            "status": "draining" if gate.draining else "ok",
            "version": __version__,
            "draining": gate.draining,
            "subscribers": bus.subscriber_count,
            "backlog": queue.backlog_health(repo=repo),
            "wakes": queue.wake_metrics(),
            "loops": {
                name: health.to_dict() for name, health in loop_health.items()
            },
            "slot": _slot_descriptor(request.app.state),
        }

    @app.get("/events")
    async def events_stream() -> StreamingResponse:
        async def gen():
            async for event in bus.subscribe():
                yield sse_format(event)

        return StreamingResponse(gen(), media_type="text/event-stream")
