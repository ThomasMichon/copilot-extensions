"""Defer Python termination handlers until native child custody is established."""

from __future__ import annotations

import signal
import sys
import threading
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from types import FrameType


class SpawnSignals:
    def __init__(self) -> None:
        self.previous: dict[int, Callable[[int, FrameType | None], object]] = {}
        self.pending: list[tuple[int, FrameType | None]] = []
        self.restored = False
        if sys.platform == "win32" or threading.current_thread() is not threading.main_thread():
            return
        try:
            for signum in (signal.SIGINT, signal.SIGTERM):
                handler = signal.getsignal(signum)
                if callable(handler):
                    self.previous[signum] = handler
                    signal.signal(signum, self.defer)
        except BaseException:
            self.restore()
            raise

    def defer(self, signum: int, frame: FrameType | None) -> None:
        if all(pending_signal != signum for pending_signal, _ in self.pending):
            self.pending.append((signum, frame))

    def restore(self) -> None:
        if not self.restored:
            self.restored = True
            if not self.previous:
                return
            mask = getattr(signal, "pthread_sigmask")
            previous_mask = mask(getattr(signal, "SIG_BLOCK"), set(self.previous))
            try:
                for signum, handler in self.previous.items():
                    signal.signal(signum, handler)
            finally:
                mask(getattr(signal, "SIG_SETMASK"), previous_mask)

    def finish(self) -> None:
        self.restore()
        pending, self.pending = self.pending, []
        for signum, frame in pending:
            self.previous[signum](signum, frame)


@contextmanager
def defer_termination() -> Iterator[None]:
    from agent_procutil.owned_process import OwnedCleanupError

    guard = SpawnSignals()
    try:
        yield
    finally:
        error = sys.exc_info()[1]
        try:
            guard.restore()
        except BaseException:
            if isinstance(error, OwnedCleanupError):
                raise error
            raise
        if not isinstance(error, OwnedCleanupError):
            guard.finish()
