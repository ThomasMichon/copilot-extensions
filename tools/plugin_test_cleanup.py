"""Bounded cleanup after a runner's existing process-settlement gate."""

from __future__ import annotations

import sys
import time
from collections.abc import Callable
from pathlib import Path
from typing import TYPE_CHECKING, Any, TypeVar, cast

if __package__ and not TYPE_CHECKING:
    from .native_test_paths import NativeTemporaryDirectory
else:
    from native_test_paths import NativeTemporaryDirectory

_T = TypeVar("_T")


class SandboxCleanupError(OSError):
    """Cleanup exhausted its retry; an enclosing owner must retain the files."""


def reap_controller(owner: Any, proc: Any, *, qualification: Any, deadline: float | None) -> None:
    def remaining() -> float:
        until = qualification.boundary.whole_deadline if qualification is not None else deadline
        return max(0.0, min(5.0, until - owner.time.monotonic())) if until is not None else 5.0
    try:
        proc.wait(timeout=remaining())
    except owner.subprocess.TimeoutExpired:
        if qualification is not None:
            qualification.forced_termination = True
        try:
            proc.kill()
            proc.wait(timeout=remaining())
        except (OSError, owner.subprocess.TimeoutExpired, KeyboardInterrupt) as exc:
            raise owner.UnreapedProcessError("Controller process could not be reaped") from exc
    except (OSError, KeyboardInterrupt) as exc:
        raise owner.UnreapedProcessError("Controller process could not be reaped") from exc
    if qualification is not None:
        qualification.worker_reaped = True
        handle = getattr(proc, "_handle", None)
        if handle is not None and not qualification.worker_handle_closed:
            try:
                handle.Close()
                qualification.worker_handle_closed = True
            except OSError as exc:
                raise owner.UnreapedProcessError("Original controller handle could not be closed") from exc
        qualification.boundary.require(owner.time.monotonic)


def _cleanup_with_windows_retry(cleanup: Callable[[], _T], *, deadline: float | None = None) -> _T:
    retry_deadline = None
    while True:
        try:
            if deadline is not None and time.monotonic() >= deadline:
                raise SandboxCleanupError("sandbox cleanup exhausted the original deadline")
            result = cleanup()
            if deadline is not None and time.monotonic() >= deadline:
                raise TimeoutError("sandbox cleanup completed after the original deadline")
            return result
        except OSError as error:
            if isinstance(error, SandboxCleanupError) or (deadline is not None and isinstance(error, TimeoutError)):
                raise
            if sys.platform != "win32" or getattr(error, "winerror", None) not in (5, 32, 33):
                raise
            now = time.monotonic()
            if retry_deadline is None:
                retry_deadline = min(now + 5, deadline) if deadline is not None else now + 5
                print(
                    "[WARN] Windows sandbox cleanup temporarily unavailable; "
                    "retrying for at most 5 seconds.",
                    file=sys.stderr,
                )
            remaining = retry_deadline - now
            if remaining <= 0:
                raise SandboxCleanupError(
                    f"Windows sandbox cleanup retry expired: {error}",
                ) from error
            time.sleep(min(0.05, remaining))


def cleanup_reaped_directory(path: Path | str) -> None:
    """Remove a runner-owned directory only after its caller verifies reaping."""
    _cleanup_with_windows_retry(lambda: cast(Callable[[Path | str], None], NativeTemporaryDirectory._rmtree)(path))
