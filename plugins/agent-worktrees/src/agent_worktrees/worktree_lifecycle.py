"""Per-worktree exclusion between native launch and checkout retirement."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import time

from single_instance_lease import AlreadyRunningError, SingleInstance


@contextmanager
def guard(path: Path, *, timeout: float = 3.0):
    from .launch_seed_state import state_path

    lease = SingleInstance(
        state_path(path).parent, service="worktree lifecycle",
        lock_name="lifecycle.lock",
    )
    deadline = time.monotonic() + timeout
    while True:
        try:
            lease.acquire()
            break
        except AlreadyRunningError as exc:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError("Worktree launch or retirement is in progress") from exc
            time.sleep(min(0.01, remaining))
    try:
        yield
    finally:
        lease.release()
