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
            # Phase 3a: advertises /events?ready_frame=1 support so a relay
            # client can gate on genuine daemon support before waiting for
            # the frame, rather than assuming every daemon it talks to is new
            # enough (a daemon that doesn't advertise this never receives the
            # request parameter either -- see client.py's stream_events()).
            "events_ready_frame": True,
            # Advertises that /tasks/{id}/steer recognizes and dedups a
            # repeated idempotency_key, so a client only retries an ambiguous
            # (connected-but-the-response-timed-out) submission once it has
            # confirmed the coordinator it's actually talking to can safely
            # absorb that retry -- an older coordinator mid zero-downtime
            # update ignores the unrecognized field entirely and would
            # otherwise record a second, duplicate answer (see
            # client_steering.py's steer()).
            "steer_idempotency_key": True,
        }

    @app.get("/events")
    async def events_stream(ready_frame: bool = False) -> StreamingResponse:
        async def gen():
            # Opt-in via the request only (never unconditional): an
            # already-installed old client never sends ``ready_frame=1`` and
            # therefore never receives this frame from any daemon, old or
            # new -- see the design doc's version-skew rationale.
            frame = {"type": "ready"} if ready_frame else None
            async for event in bus.subscribe(ready_frame=frame):
                yield sse_format(event)

        return StreamingResponse(gen(), media_type="text/event-stream")
