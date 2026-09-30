"""Recovery sweep for detached `run --detach` waiters after coordinator outages."""

from __future__ import annotations

import logging
from collections.abc import Callable

from . import remote_dispatch
from .queue import TaskQueue

log = logging.getLogger("agent-dispatch.run-waiter-recovery")


def recover_run_waiters(
    queue: TaskQueue,
    *,
    process_exists: Callable[[int], bool],
    start_token_for_pid: Callable[[int], str | None],
    current_machine: str | None = None,
) -> dict[str, int]:
    """Wake tasks whose detached `run` waiter died while the coordinator was down."""
    if current_machine is None:
        current_machine = remote_dispatch.local_machine()
    counts = {"checked": 0, "live": 0, "unknown": 0, "recovered": 0}
    for waiter in queue.list_active_run_waiters():
        counts["checked"] += 1
        host = waiter.get("host")
        if not current_machine or not host or host != current_machine:
            counts["unknown"] += 1
            continue
        try:
            exists = process_exists(int(waiter["pid"]))
            current = start_token_for_pid(int(waiter["pid"]))
        except Exception:
            counts["unknown"] += 1
            continue
        if exists and not waiter.get("start_token"):
            counts["unknown"] += 1
            continue
        if exists and current is None:
            counts["unknown"] += 1
            continue
        if exists and current == waiter.get("start_token"):
            counts["live"] += 1
            continue
        retired = queue.recover_dead_run_waiter(
            waiter["task_id"],
            generation=int(waiter["generation"]),
            reason="infrastructure failure",
            message=(
                "The detached wait this task was relying on disappeared while "
                "agent-dispatch was unavailable. Infrastructure failure. Re-check the "
                "external state and decide whether to run the wait again."
            ),
            sender="agent-dispatch-run-waiter-recovery",
        )
        if retired is None:
            continue
        counts["recovered"] += 1
    return counts
