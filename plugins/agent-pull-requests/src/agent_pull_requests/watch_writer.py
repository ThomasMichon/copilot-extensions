"""Native writer ownership for the governed watch-owner adapter.

This boundary does not grant installation or transaction authority. Its caller
must supply a validator for each transition, including confirmed-successor-exit
authorization before a released predecessor may resume.
"""

from __future__ import annotations

import os
import threading
import time
from collections.abc import Callable, Iterator
from contextlib import contextmanager
from pathlib import Path

from single_instance_lease import SingleInstance

from .watch_daemon import Fetch, NativeFetch, Notify, WatchDaemon
from .watch_notification import default_notify, positive_seconds


class WatchWriter:
    """Bind one inactive core to its exact state root and native writer lease."""

    def __init__(
        self, *, root: Path, authorize: Callable[[str], None], fetch: Fetch,
        notify: Notify = default_notify, poll_interval: float = 30.0,
        native_fetch: NativeFetch | None = None,
    ) -> None:
        if not root.is_absolute():
            raise ValueError("watch writer state root must be absolute")
        if not callable(authorize):
            raise TypeError("watch writer requires an authority validator")
        self.root = root
        self.authorize = authorize
        self.daemon = WatchDaemon(
            fetch=fetch, notify=notify, poll_interval=poll_interval,
            active=False, state_root=root,
            native_fetch=native_fetch,
        )
        self._lease = SingleInstance(root, service="agent-pull-requests-watch")
        self._lifecycle = threading.Lock()
        self._creating_pid = os.getpid()
        self._read_ready = False
        self._drain_started = False
        self._closed = False

    @property
    def writer_held(self) -> bool:
        self._assert_process()
        return self._lease.held

    @property
    def read_ready(self) -> bool:
        self._assert_process()
        return self._read_ready and self.writer_held and not self._closed

    def _assert_process(self) -> None:
        if os.getpid() != self._creating_pid:
            raise RuntimeError("watch writer belongs to another process")

    @staticmethod
    def _remaining(deadline: float) -> float:
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            raise TimeoutError("watch writer lifecycle deadline expired")
        return remaining

    @contextmanager
    def _operation(self, timeout: float) -> Iterator[float]:
        self._assert_process()
        positive_seconds(timeout)
        deadline = time.monotonic() + timeout
        if not self._lifecycle.acquire(timeout=timeout):
            raise TimeoutError("watch writer lifecycle is busy")
        try:
            self._remaining(deadline)
            yield deadline
        finally:
            self._lifecycle.release()

    def _validate(self, operation: str, deadline: float) -> None:
        if self._closed:
            raise RuntimeError("watch writer is closed")
        if self.authorize(operation) is not None:
            raise PermissionError("watch writer authority validation did not complete")
        self._remaining(deadline)

    def _acquire_and_reload(self, deadline: float) -> None:
        self._read_ready = False
        if not self.writer_held:
            self._lease.acquire()
        self._remaining(deadline)
        self.daemon.reload_state(timeout=self._remaining(deadline))
        self._remaining(deadline)
        self._read_ready = True

    def adopt(self, *, timeout: float) -> None:
        """Acquire the real writer lock before reading the final snapshot."""
        with self._operation(timeout) as deadline:
            self._validate("adopt", deadline)
            if not self.daemon.quiescent:
                raise RuntimeError("watch writer adoption requires completed quiescence")
            self._acquire_and_reload(deadline)

    def activate(self, *, timeout: float) -> None:
        """Authorize effects separately from a read-ready writer acquisition."""
        with self._operation(timeout) as deadline:
            self._validate("activate", deadline)
            if not self.read_ready:
                raise RuntimeError("watch writer must be read-ready before activation")
            self.daemon.resume_effects(timeout=self._remaining(deadline))
            self._remaining(deadline)
            self._drain_started = False

    def drain_and_release(self, *, timeout: float) -> None:
        """Never release on incomplete drain or failed post-drain authorization."""
        with self._operation(timeout) as deadline:
            self._validate("drain", deadline)
            if not self.writer_held:
                raise RuntimeError("watch writer does not hold predecessor authority")
            self._drain_started = True
            self.daemon.quiesce(timeout=self._remaining(deadline))
            self._remaining(deadline)
            self._validate("release", deadline)
            self._lease.release()
            self._read_ready = False

    def resume(self, *, timeout: float) -> None:
        """Resume retained work, or reacquire/reload after authorized rollback.

        The caller's ``resume`` authorization must prove exact successor exit
        after any transfer. Lease availability alone is not retirement evidence.
        """
        with self._operation(timeout) as deadline:
            self._validate("resume", deadline)
            if not self._drain_started:
                raise RuntimeError("watch writer is not a drained predecessor")
            if not self.writer_held:
                self._acquire_and_reload(deadline)
            if not self.read_ready:
                raise RuntimeError("watch writer must be read-ready before resumption")
            self.daemon.resume_effects(timeout=self._remaining(deadline))
            self._remaining(deadline)
            self._drain_started = False

    def compute(self, kind: str, payload: dict) -> dict:
        self._assert_process()
        if kind in {"register", "unregister"} and not self.read_ready:
            return {"error": "watch writer is not read-ready", "retryable": True}
        return self.daemon.compute(kind, payload)

    def require_passive(self) -> None:
        """Never treat a journal phase as proof that writer/effect adoption did not run."""
        if (
            self.writer_held or self.read_ready or self.daemon.effects_enabled
            or not self.daemon.quiescent
        ):
            raise PermissionError("watch cleanup requires an effect-free passive writer")

    def close_passive(self, *, timeout: float) -> None:
        with self._operation(timeout) as deadline:
            self._validate("cleanup-stop", deadline)
            self.require_passive()
            self.daemon.close(timeout=self._remaining(deadline))
            self._validate("cleanup-stop", deadline)
            self.require_passive()
            self._closed = True

    def close(self, *, timeout: float) -> None:
        """Retain the native lease when callbacks or accepted writes remain live."""
        with self._operation(timeout) as deadline:
            if self._closed:
                return
            self.daemon.close(timeout=self._remaining(deadline))
            self._remaining(deadline)
            self._lease.release()
            self._read_ready = False
            self._closed = True

    def withdraw(self, *, timeout: float) -> None:
        """Revalidate stop custody after all accepted work has quiesced."""
        with self._operation(timeout) as deadline:
            self._validate("withdraw", deadline)
            self.daemon.close(timeout=self._remaining(deadline))
            self._validate("withdraw", deadline)
            self._lease.release()
            self._read_ready = False
            self._closed = True
