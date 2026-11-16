"""Bounded retirement of one demoted central process, never its descendants."""

from __future__ import annotations

import time
from collections.abc import Callable
from pathlib import Path
from typing import Any, Protocol

from agent_procutil import _daemon_instance_identity, process_retirement
from zdd import diagnostics, process_exit, routing
from zdd.routing import Endpoint


class _Client(Protocol):
    def health(self) -> dict[str, Any]: ...


def retire_predecessor(
    root: Path, previous: Endpoint, successor: Endpoint, *,
    make_client: Callable[[Endpoint], _Client], graceful_timeout: float = 5.0,
) -> dict[str, Any]:
    """Keep cleanup failure separate from the incoming generation's readiness."""
    from .service_process_cli import _pid_is_agent_bridge

    def verify_route(*, require_previous: bool = True) -> None:
        table = routing.read_table(root, strict=True) or {}
        routing._validate_endpoint_rows(table)
        if (
            table.get("active") != successor.to_dict()
            or (require_previous and table.get("previous") != previous.to_dict())
            or successor.generation <= previous.generation
            or successor.pid == previous.pid
            or successor.pid is None
            or not successor.process_start_time
            or diagnostics.process_start_time(successor.pid) != successor.process_start_time
        ):
            raise RuntimeError("central retirement ownership changed")

    def verify_previous() -> None:
        if (
            not previous.pid or not previous.process_start_time
            or diagnostics.process_start_time(previous.pid) != previous.process_start_time
            or not _pid_is_agent_bridge(previous.pid, timeout=2.0)
        ):
            raise RuntimeError("central predecessor identity is unavailable")

    try:
        verify_route(require_previous=False)
        if not previous.pid or not previous.process_start_time:
            raise RuntimeError("central predecessor has no native identity")
        if process_exit.observe_original_exit(previous.pid, previous.process_start_time):
            return {"exit_confirmed": True, "forced": False}
        verify_previous()
        body = make_client(successor).health()
        slot = body.get("slot")
        if (
            body.get("status") != "ok" or body.get("service") != "agent-bridge"
            or body.get("ready") is not True or body.get("draining") is True
            or not isinstance(slot, dict) or slot.get("pid") != successor.pid
            or slot.get("role") != "active" or slot.get("active") != successor.to_dict()
        ):
            raise RuntimeError("central successor is not ready")
        birth = _daemon_instance_identity.birth_identity(previous.pid)
        with process_retirement.PinnedProcess(previous.pid, birth) as pinned:
            verify_previous()
            verify_route()
            if pinned.exited():
                return {"exit_confirmed": True, "forced": False}
            # Observe self-retirement only. An old /shutdown request cannot be
            # fenced against reselection or recalled after an HTTP timeout.
            if pinned.exited(graceful_timeout):
                return {"exit_confirmed": True, "forced": False}
            # Serialize the irreversible signal with route publication. The
            # native handle/pidfd prevents PID reuse; no group or tree is signaled.
            with routing.routing_lock(root, deadline=time.monotonic() + 2.0):
                verify_route()
                verify_previous()
                pinned.terminate(timeout=5.0)
            return {"exit_confirmed": True, "forced": True}
    except Exception as exc:
        return {"exit_confirmed": False, "reason": type(exc).__name__}
