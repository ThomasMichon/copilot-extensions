"""Retain mux instance custody until its closed server's handlers retire."""

from __future__ import annotations

import logging
import threading
from collections.abc import Callable
from pathlib import Path

from agent_procutil.daemon_instances import admit_daemon_instance
from work_coalescing_singleton import CoalescingServer

LOG = logging.getLogger(__name__)


class ActiveHandlerTracker:
    """Bounded apply/discovery fence, not full accepted-handler settlement."""

    def __init__(self) -> None:
        self._lock = threading.Lock()
        self._count = 0
        self._drained = threading.Event()
        self._drained.set()

    def enter(self) -> None:
        with self._lock:
            self._count += 1
            self._drained.clear()

    def exit(self) -> None:
        with self._lock:
            self._count -= 1
            if self._count <= 0:
                self._drained.set()

    def wait_for_drain(self, timeout: float) -> bool:
        return self._drained.wait(timeout=timeout)


class MuxHandlerLifetime:
    """One runtime-owned retirement worker, independent of its caller's lifetime."""

    def __init__(
        self, root: Path, source: str, close_owned_work: Callable[[], None],
        tracker: ActiveHandlerTracker,
    ) -> None:
        self.server: CoalescingServer | None = None
        self._close_owned_work = close_owned_work
        self._tracker = tracker
        self.guard = admit_daemon_instance(
            "worktree-manager-mux", runtime_root=root, source=source, purpose="production",
        )
        self._stop = threading.Event()
        self._poll = threading.Event()
        self.closed = threading.Event()
        self.owned_work_closed = threading.Event()
        self._work_lock = threading.Lock()
        self.settled = threading.Event()
        try:
            self.thread = threading.Thread(
                target=self._retire, name="mux-handler-retirement", daemon=True,
            )
            self.thread.start()
        except BaseException:
            # No socket or application work exists until this owner is running.
            self.guard.close()
            raise

    def shutdown(self, timeout: float) -> None:
        if self._stop.is_set():
            return
        self._stop.set()
        self.closed.wait(timeout)

    def close_owned_work(self) -> None:
        with self._work_lock:
            if not self.owned_work_closed.is_set():
                self._close_owned_work()
                self.owned_work_closed.set()

    def _retire(self) -> None:
        self._stop.wait()
        reported = False
        while True:
            try:
                server = self.server
                if not self.closed.is_set():
                    if server is not None:
                        server.close_admission("shutdown")
                        server.close()
                        # close() uses bounded joins. Do not mistake a late
                        # accept/reaper thread for confirmed native retirement.
                        if any(thread.is_alive() for thread in (
                            server._serve_thread, server._reap_thread,
                        )):
                            self._poll.wait(0.05)
                            continue
                    self.closed.set()
                if not self.owned_work_closed.is_set():
                    self.close_owned_work()
                count = 0 if server is None else server.active_handler_count()
                if type(count) is int and count == 0 and self._tracker.wait_for_drain(0):
                    self.guard.close()
                    self.settled.set()
                    return
            except BaseException:
                if not reported:
                    LOG.exception("Mux retirement is incomplete; retaining instance custody")
                    reported = True
            # There is no production expiry or inferred zero for unknown state.
            self._poll.wait(0.05)
