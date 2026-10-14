"""Kernel-owned exclusion between creation recovery and seed handoff."""

from __future__ import annotations

from collections.abc import Iterator
from contextlib import contextmanager
from pathlib import Path
import time

from single_instance_lease import AlreadyRunningError, SingleInstance


class LaunchAdmissionBusy(ValueError):
    """Another launch mutation or creation repair owns this worktree."""


def _lease(path: Path) -> SingleInstance:
    from .launch_seed_state import state_path

    return SingleInstance(
        state_path(path).parent, service="worktree launch admission",
        lock_name="launch-admission.lock",
    )


@contextmanager
def guard(path: Path, *, timeout: float = 0.0) -> Iterator[None]:
    lease = _lease(path)
    deadline = time.monotonic() + timeout
    while True:
        try:
            lease.acquire()
            break
        except AlreadyRunningError as exc:
            if time.monotonic() >= deadline:
                raise LaunchAdmissionBusy(
                    "Worktree launch admission is busy; the prompt remains staged. "
                    "Retry after the current launch mutation or creation repair completes."
                ) from exc
            time.sleep(min(0.01, max(0.0, deadline - time.monotonic())))
    try:
        yield
    finally:
        lease.release()


def check(path: Path) -> None:
    if _lease(path).lock_path.exists():
        with guard(path):
            pass
