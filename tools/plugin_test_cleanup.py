"""Bounded cleanup after a runner's existing process-settlement gate."""

from __future__ import annotations

import sys
import tempfile
import time
from collections.abc import Callable
from pathlib import Path
from typing import TypeVar

_T = TypeVar("_T")


class SandboxCleanupError(OSError):
    """Cleanup exhausted its retry; an enclosing owner must retain the files."""


def _cleanup_with_windows_retry(cleanup: Callable[[], _T]) -> _T:
    retry_deadline = None
    while True:
        try:
            return cleanup()
        except OSError as error:
            if sys.platform != "win32" or getattr(error, "winerror", None) not in (5, 32, 33):
                raise
            now = time.monotonic()
            if retry_deadline is None:
                retry_deadline = now + 5
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
    _cleanup_with_windows_retry(lambda: tempfile.TemporaryDirectory._rmtree(path))
