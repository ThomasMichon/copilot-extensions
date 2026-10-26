"""Foreground watch owners retain admission until their original work settles."""

from __future__ import annotations

import threading
import time


class WatchRetirement:
    """Retry bounded cleanup without delivering main-thread interrupts to joins."""

    def __init__(self, *, timeout: float = 5.0, close_owner=None) -> None:
        self._server = None
        self._handlers: set[threading.Thread] = set()
        self.close_owner = close_owner
        self.release = None
        self.timeout = timeout
        self._error: BaseException | None = None
        self._lock = threading.Lock()

    def __enter__(self):
        return self

    @property
    def server(self):
        return self._server

    @server.setter
    def server(self, server) -> None:
        transport = getattr(server, "_server", None)
        if transport is not None:
            process = transport.process_request_thread

            def accepted(*args):
                with self._lock:
                    self._handlers.intersection_update(threading.enumerate())
                    self._handlers.add(threading.current_thread())
                process(*args)

            transport.process_request_thread = accepted
        self._server = server

    def _remember(self, error: BaseException) -> None:
        with self._lock:
            if self._error is None:
                self._error = error

    def _attempt(self, operation) -> None:
        while True:
            try:
                operation()
                return
            except BaseException as error:
                self._remember(error)
                time.sleep(0.02)

    def _settle(self, done: threading.Event) -> None:
        server = self.server
        if server is not None:
            # A failed close is UNKNOWN, including when no handler is counted.
            self._attempt(lambda: server.close_admission(reason="shutdown"))
            self._attempt(server.close)
            deadline = time.monotonic() + self.timeout

            def drain():
                while server.active_handler_count():
                    if time.monotonic() >= deadline:
                        self._remember(TimeoutError("PR watch request handlers did not drain"))
                    time.sleep(0.02)
                # Native registry membership is not CPython's interruptible
                # Thread.join/is_alive bookkeeping.
                while any(thread in threading.enumerate() for thread in (
                    server._serve_thread, server._reap_thread, *self._handlers,
                )):
                    time.sleep(0.02)

            self._attempt(drain)
        if self.close_owner is not None:
            self._attempt(self.close_owner)
        if self.release is not None:
            self._attempt(self.release)
        done.set()

    def __exit__(self, kind, error, traceback) -> None:
        if error is not None:
            self._remember(error)
        done = threading.Event()
        worker = threading.Thread(
            target=self._settle, args=(done,), name="pr-watch-retirement", daemon=False,
        )
        while True:
            try:
                worker.start()
                break
            except (KeyboardInterrupt, SystemExit) as interruption:
                self._remember(interruption)
                # Thread.start may be interrupted AFTER native creation. Never
                # create a competing cleanup owner in that unknown interval.
                while not worker._started.is_set():
                    try:
                        worker._started.wait(0.02)
                    except BaseException as repeated:
                        self._remember(repeated)
                break
            except BaseException as failure:
                self._remember(failure)
                if worker._started.is_set():
                    break
                time.sleep(0.02)
        while True:
            try:
                if done.wait(0.02) and worker not in threading.enumerate():
                    break
            except BaseException as interruption:
                self._remember(interruption)
        if self._error is not None:
            raise self._error
