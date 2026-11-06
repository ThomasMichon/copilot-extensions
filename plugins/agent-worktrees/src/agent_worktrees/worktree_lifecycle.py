"""Per-worktree exclusion between native launch and checkout retirement."""

from __future__ import annotations

from contextlib import contextmanager
from pathlib import Path
import time

from single_instance_lease import AlreadyRunningError, SingleInstance


@contextmanager
def guard(path: Path, *, timeout: float = 3.0, relocation: bool = False):
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
        if not relocation:
            from .checkout_relocation_state import require_idle

            require_idle(path)
        yield
    finally:
        lease.release()
