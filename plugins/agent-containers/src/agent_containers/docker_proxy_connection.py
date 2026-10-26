"""Connection-local native child and pump custody for the SSH broker."""

from __future__ import annotations

import contextlib
import os
import socket
import subprocess
import threading
from collections.abc import Callable

from agent_procutil import no_window_kwargs
from agent_procutil.owned_process import OwnedCleanupError

if os.name == "nt":
    from agent_procutil._windows_caller_owned import spawn as spawn_sync_in_kill_on_close_job
else:
    from agent_procutil import spawn_sync_in_kill_on_close_job


def settle(action: Callable[[], object]) -> None:
    """Keep custody through interrupted or temporarily unsuccessful cleanup."""
    retry = threading.Event()
    failed = False
    while True:
        try:
            if failed:
                retry.wait(0.05)
            action()
            return
        except BaseException:
            failed = True


def close_socket(connection: socket.socket) -> None:
    with contextlib.suppress(OSError):
        connection.shutdown(socket.SHUT_RDWR)
    connection.close()


class Worker:
    def __init__(self, target: Callable[[], None], name: str) -> None:
        self.done = threading.Event()

        def run() -> None:
            try:
                target()
            finally:
                self.done.set()

        self.thread = threading.Thread(target=run, name=name, daemon=True)

    def start(self) -> None:
        try:
            self.thread.start()
        except BaseException as error:
            if self.thread.ident is None and not isinstance(error, (KeyboardInterrupt, SystemExit)):
                self.done.set()
            # An interrupt inside Thread.start can precede publication of its
            # identity. Unknown creation is not proof that its target is absent.
            raise

    def join(self) -> None:
        # Thread.join interrupted by SIGINT can mark a still-running thread
        # stopped on CPython. The target's own receipt, not is_alive, gates exit.
        self.done.wait()
        if self.thread.ident is not None:
            self.thread.join()


class Connection:
    def __init__(
        self, connection: socket.socket, command: list[str],
        inbound: Callable, outbound: Callable,
    ) -> None:
        self.socket = connection
        self.command = command
        self.inbound = inbound
        self.outbound = outbound
        self.stopping = threading.Event()
        self.worker = Worker(self.run, "container-ssh-connection")
        self.error: BaseException | None = None

    def stop(self) -> None:
        self.stopping.set()
        close_socket(self.socket)

    def run(self) -> None:
        process = job = None
        pumps: list[Worker] = []
        try:
            if self.stopping.is_set():
                return
            process, job = spawn_sync_in_kill_on_close_job(
                self.command, require_job=True, allow_breakaway=False,
                stdin=subprocess.PIPE, stdout=subprocess.PIPE,
                stderr=subprocess.DEVNULL, bufsize=0, **no_window_kwargs(),
            )
            if process.stdin is None or process.stdout is None:
                raise RuntimeError("container SSH broker did not receive Docker pipes")
            for name, target, args in (
                ("container-ssh-inbound", self.inbound, (self.socket, process.stdin)),
                ("container-ssh-outbound", self.outbound, (process.stdout, self.socket)),
            ):
                pump = Worker(lambda target=target, args=args: target(*args), name)
                pumps.append(pump)
                pump.start()
            while not self.stopping.is_set():
                try:
                    process.wait(timeout=0.1)
                    break
                except subprocess.TimeoutExpired:
                    pass
        except BaseException as error:
            if isinstance(error, OwnedCleanupError):
                process, job = error.process, error.job
            self.error = error
        finally:
            def cleanup() -> None:
                if process is not None:
                    if job is not None and job.handle:
                        job.terminate_and_wait(2)
                    elif job is None and process.poll() is None:
                        with contextlib.suppress(ProcessLookupError):
                            process.terminate()
                    try:
                        process.wait(timeout=2)
                    except subprocess.TimeoutExpired:
                        process.kill()
                        process.wait(timeout=2)
                if len(pumps) == 2:
                    # Preserve the final SSH bytes on ordinary child exit.
                    settle(pumps[1].join)
                settle(self.stop)
                for pump in pumps:
                    settle(pump.join)
                if process is not None:
                    # Never close a pipe while a pump may hold its lock.
                    for stream in (process.stdin, process.stdout):
                        if stream is not None:
                            settle(stream.close)
                    close = getattr(process, "close", None)
                    if close is not None:
                        settle(close)

            settle(cleanup)
