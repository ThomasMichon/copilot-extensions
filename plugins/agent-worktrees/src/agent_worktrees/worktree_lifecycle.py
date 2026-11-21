"""Per-worktree exclusion between native launch and checkout retirement."""

from __future__ import annotations

from contextlib import contextmanager
from collections.abc import Iterator
from pathlib import Path
import os
import threading
import time

from single_instance_lease import AlreadyRunningError, SingleInstance

_held = threading.local()


def _require_idle(path: Path, relocation: bool) -> None:
    if not relocation:
        from .checkout_relocation_state import require_idle
        try:
            require_idle(path)
        except (ValueError, OSError) as exc:
            raise TimeoutError(f"Worktree relocation blocks lifecycle mutation: {exc}") from exc


@contextmanager
def guard(
    path: Path, *, timeout: float = 3.0, relocation: bool = False, reentrant: bool = False,
) -> Iterator[None]:
    from .launch_seed_state import state_path

    key = (os.getpid(), os.path.normcase(str(path.resolve())))
    held: set[tuple[int, str]] = getattr(_held, "paths", set())
    _held.paths = held
    if key in held and reentrant:
        _require_idle(path, relocation)
        yield
        return
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
        _require_idle(path, relocation)
        held.add(key)
        yield
    finally:
        held.discard(key)
        lease.release()
