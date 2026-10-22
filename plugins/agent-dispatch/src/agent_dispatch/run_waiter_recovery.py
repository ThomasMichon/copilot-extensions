"""Recovery sweep for detached `run --detach` waiters after coordinator outages."""

from __future__ import annotations

import logging
import asyncio
from collections.abc import Callable

from . import remote_dispatch
from .queue import TaskQueue

log = logging.getLogger("agent-dispatch.run-waiter-recovery")
DEFAULT_RUN_WAITER_ARM_GRACE_SECONDS = 60.0


async def maintain_delegated_recovery(queue: TaskQueue, recover, *, interval: float = 60.0,
                                      sleep=None) -> None:
    """Continue the existing recovery owner while delegated waits can expire."""
    sleep = sleep or asyncio.sleep
    while True:
        await sleep(interval)
        waiters = await asyncio.to_thread(queue.list_active_run_waiters)
        if any(waiter.get("kind") == "delegated" for waiter in waiters):
            await recover()


def recover_run_waiters(
    queue: TaskQueue,
    *,
    process_exists: Callable[[int], bool],
    start_token_for_pid: Callable[[int], str | None],
    current_machine: str | None = None,
    arm_grace_seconds: float = DEFAULT_RUN_WAITER_ARM_GRACE_SECONDS,
    delegated_owner_probe: Callable[[], dict | None] | None = None,
) -> dict[str, int]:
    """Wake tasks whose detached `run` waiter died while the coordinator was down."""
    if current_machine is None:
        current_machine = remote_dispatch.local_machine()
    now = queue._now(None)  # queue-owned clock for tests/restart reasoning
    counts = {"checked": 0, "live": 0, "unknown": 0, "recovered": 0}
    owner_probed = False
    owner_status = None
    for waiter in queue.list_pending_run_waiters():
        counts["checked"] += 1
        if now < float(waiter["created_at"]) + max(0.01, arm_grace_seconds):
            counts["unknown"] += 1
            continue
        retired = queue.recover_preparing_run_waiter(
            waiter["task_id"],
            generation=int(waiter["generation"]),
            reason="waiter never armed",
            message=(
                "The detached wait this task was trying to start never finished"
                " arming before agent-dispatch was interrupted. Re-check the"
                " external state and decide whether to run the wait again."
            ),
            sender="agent-dispatch-run-waiter-recovery",
        )
        if retired is None:
            continue
        counts["recovered"] += 1
    for waiter in queue.list_active_run_waiters():
        counts["checked"] += 1
        if waiter.get("kind") == "delegated":
            from .delegated_waiter_recovery import probe_local_owner, recovery_reason
            if not owner_probed and waiter.get("host") == current_machine:
                owner_status = (delegated_owner_probe or probe_local_owner)()
                owner_probed = True
            reason = recovery_reason(waiter, now=now, current_machine=current_machine,
                                     owner_status=owner_status)
            if reason is None:
                # This is not PID liveness. The finite deadline remains the
                # backstop even when the observing owner is on another host.
                counts["unknown"] += 1
                continue
            retired = queue.recover_dead_run_waiter(
                waiter["task_id"], generation=int(waiter["generation"]), reason=reason,
                message=f"Delegated wait recovery: {reason}. Re-check external state before continuing.",
                sender="agent-dispatch-run-waiter-recovery",
            )
            if retired is not None:
                log.warning("Delegated run waiter recovered: %s", reason)
                counts["recovered"] += 1
            continue
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
