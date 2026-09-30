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
    wake_worktree: Callable[[str, str], bool],
    release_claim: Callable[[str, str], object | None],
    current_machine: str | None = None,
) -> dict[str, int]:
    """Wake tasks whose detached `run` waiter died while the coordinator was down."""
    if current_machine is None:
        current_machine = remote_dispatch.local_machine()
    counts = {"checked": 0, "live": 0, "unknown": 0, "recovered": 0}
    for waiter in queue.list_active_run_waiters():
        counts["checked"] += 1
        host = waiter.get("host")
        if not current_machine or (host and host != current_machine):
            counts["unknown"] += 1
            continue
        try:
            exists = process_exists(int(waiter["pid"]))
            current = start_token_for_pid(int(waiter["pid"]))
        except Exception:
            counts["unknown"] += 1
            continue
        if exists and current is None:
            counts["unknown"] += 1
            continue
        if exists and current == waiter.get("start_token"):
            counts["live"] += 1
            continue
        if exists:
            current = current
        elif current is not None:
            current = current
        else:
            current = None
        retired = queue.retire_run_waiter(
            waiter["task_id"],
            pid=int(waiter["pid"]),
            host=host,
            start_token=waiter.get("start_token"),
            reason="infrastructure failure",
        )
        if retired is None:
            continue
        message = (
            "The detached wait this task was relying on disappeared while "
            "agent-dispatch was unavailable. Infrastructure failure. Re-check the "
            "external state and decide whether to run the wait again."
        )
        wake_worktree(str(waiter["resume_worktree"]), message)
        release_claim(str(waiter["task_id"]), str(waiter["resume_worktree"]))
        counts["recovered"] += 1
    return counts
