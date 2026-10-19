"""Bounded children in owned roots or an explicitly inherited containment scope."""

from __future__ import annotations

import math
import os
import selectors
import signal
import subprocess
import threading
import time

from agent_procutil import no_window_kwargs, spawn_sync_in_kill_on_close_job


def _require_windows_job() -> None:
    import ctypes
    from ctypes import wintypes
    from agent_procutil import _kernel32

    kernel = _kernel32()
    kernel.GetCurrentProcess.argtypes = []
    kernel.GetCurrentProcess.restype = wintypes.HANDLE
    kernel.IsProcessInJob.argtypes = [
        wintypes.HANDLE, wintypes.HANDLE, ctypes.POINTER(wintypes.BOOL),
    ]
    kernel.IsProcessInJob.restype = wintypes.BOOL
    contained = wintypes.BOOL()
    if not kernel.IsProcessInJob(kernel.GetCurrentProcess(), None, ctypes.byref(contained)):
        raise ctypes.WinError(ctypes.get_last_error())
    if not contained.value:
        raise RuntimeError("contained worker requires an existing Windows Job")
    # NULL-job limit queries can describe another Job in the nested/venv
    # chain. Strictness is enforced at every producer's suspended spawn,
    # using its retained Job handle, never inferred from that query.


def require_contained_worker() -> None:
    """Validate kernel posture; this lifetime contract grants no application authority."""
    if os.name == "nt":
        _require_windows_job()
    else:
        pid = os.getpid()
        if os.getpgrp() != pid or os.getsid(0) != pid:
            raise RuntimeError("contained worker requires its outer owner's session/group root")


def _remaining(deadline: float, argv: list[str], timeout: float) -> float:
    remaining = deadline - time.monotonic()
    if remaining <= 0:
        raise subprocess.TimeoutExpired(argv, timeout)
    return remaining


def _observe_unreaped(process: subprocess.Popen):
    if signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError("native child custody requires the default SIGCHLD disposition")
    if process.returncode is not None:
        raise RuntimeError("native child custody was already released")
    try:
        status = os.waitid(
            os.P_PID, process.pid, os.WEXITED | os.WNOHANG | os.WNOWAIT,
        )
    except OSError as error:
        raise RuntimeError("native child custody is unconfirmed") from error
    if status is not None and status.si_pid != process.pid:
        raise RuntimeError("native child identity changed")
    return status


def _retire_posix_group(
    process: subprocess.Popen, *, inherit_process_group: bool = False,
) -> None:
    # Exclusive Popen ownership plus WNOWAIT pins the original PID through termination.
    # Never poll, communicate or wait this child before this boundary.
    _observe_unreaped(process)
    try:
        if inherit_process_group:
            os.kill(process.pid, signal.SIGKILL)
        else:
            os.killpg(process.pid, signal.SIGKILL)
    except ProcessLookupError:
        pass


def _communicate_unreaped(
    process: subprocess.Popen, *, input_text: str | None, deadline: float,
    argv: list[str], timeout: float,
) -> tuple[str | None, str | None]:
    streams = (process.stdout, process.stderr)
    output = (bytearray(), bytearray())
    data = memoryview(input_text.encode("utf-8")) if input_text is not None else None
    offset = 0
    with selectors.DefaultSelector() as selector:
        for index, stream in enumerate(streams):
            if stream is not None:
                os.set_blocking(stream.fileno(), False)
                selector.register(stream, selectors.EVENT_READ, index)
        if process.stdin is not None:
            if data:
                os.set_blocking(process.stdin.fileno(), False)
                selector.register(process.stdin, selectors.EVENT_WRITE, -1)
            else:
                process.stdin.close()
        while selector.get_map():
            for key, _mask in selector.select(_remaining(deadline, argv, timeout)):
                if key.data == -1:
                    try:
                        offset += os.write(key.fd, data[offset:offset + 65536])
                    except BlockingIOError:
                        continue
                    except BrokenPipeError:
                        offset = len(data)
                    if offset == len(data):
                        selector.unregister(key.fileobj)
                        key.fileobj.close()
                else:
                    try:
                        chunk = os.read(key.fd, 65536)
                    except BlockingIOError:
                        continue
                    if chunk:
                        output[key.data].extend(chunk)
                    else:
                        selector.unregister(key.fileobj)
    while _observe_unreaped(process) is None:
        time.sleep(min(.01, _remaining(deadline, argv, timeout)))
    return tuple(
        data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n")
        if stream is not None else None
        for data, stream in zip(output, streams)
    )


def _communicate_windows(
    process: subprocess.Popen, job, *, input_text: str | None, deadline: float,
    argv: list[str], timeout: float,
) -> tuple[str | None, str | None]:
    if job is None:
        raise RuntimeError("native child Windows Job custody is unavailable")
    stop = threading.Event()
    expired = threading.Event()
    retirement = threading.Lock()
    retired = False
    failures = []

    def retire():
        nonlocal retired
        with retirement:
            if not retired:
                job.terminate_and_wait(2.0)
                retired = True

    def supervise():
        if stop.wait(max(0.0, deadline - time.monotonic())):
            return
        expired.set()
        try:
            retire()
        except BaseException as error:
            failures.append(error)

    watchdog = (
        threading.Thread(target=supervise, name="pr-native-input-deadline", daemon=False)
        if input_text is not None else None
    )
    try:
        if watchdog is not None:
            watchdog.start()
        stdout, stderr = process.communicate(
            input_text, timeout=_remaining(deadline, argv, timeout),
        )
        completed_at = time.monotonic()
        stop.set()
    finally:
        stop.set()
        try:
            retire()
        finally:
            if watchdog is not None and watchdog.ident is not None:
                watchdog.join()
            process.wait(timeout=2.0)
            # All Job-owned pipe ends are now closed. Join readers before the
            # caller closes their streams; stdin writing ran on this caller.
            for reader in (
                getattr(process, "stdout_thread", None), getattr(process, "stderr_thread", None),
            ):
                if reader is not None:
                    reader.join()
        if failures:
            raise failures[0]
    if expired.is_set() or completed_at >= deadline:
        raise subprocess.TimeoutExpired(argv, timeout, stdout, stderr)
    return stdout, stderr


def run_owned(
    argv: list[str], *, timeout: float, input_text: str | None = None,
    capture: bool = True, inherit_process_group: bool = False,
) -> subprocess.CompletedProcess:
    """Own a finite root, or borrow its verified scope without detaching nested work.

    Borrowed POSIX children are individually retired before reaping. Only their
    outer owner may sweep the inherited group, including descendants, before
    reporting whole-scope completion. Callers retain exclusive Popen custody.
    """
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise ValueError("native child timeout must be finite and positive")
    windows = os.name == "nt"
    if type(inherit_process_group) is not bool:
        raise ValueError("inherit_process_group must be a boolean")
    if inherit_process_group:
        require_contained_worker()
    if not windows and not all(hasattr(os, name) for name in (
        "waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT",
    )):
        raise RuntimeError("native child execution requires unreaped POSIX custody")
    if not windows and signal.getsignal(signal.SIGCHLD) != signal.SIG_DFL:
        raise RuntimeError("native child custody requires the default SIGCHLD disposition")
    deadline = time.monotonic() + timeout
    process, job = spawn_sync_in_kill_on_close_job(
        argv, require_job=True, allow_breakaway=False,
        stdin=subprocess.PIPE if input_text is not None else subprocess.DEVNULL,
        stdout=subprocess.PIPE if capture else subprocess.DEVNULL,
        stderr=subprocess.PIPE if capture else subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="strict", **no_window_kwargs(),
        **({"start_new_session": True} if not windows and not inherit_process_group else {}),
    )
    try:
        if windows:
            stdout, stderr = _communicate_windows(
                process, job, input_text=input_text, deadline=deadline, argv=argv, timeout=timeout,
            )
        else:
            stdout, stderr = _communicate_unreaped(
                process, input_text=input_text, deadline=deadline, argv=argv, timeout=timeout,
            )
    finally:
        try:
            if not windows:
                _retire_posix_group(process, inherit_process_group=inherit_process_group)
                process.wait(timeout=2.0)
        finally:
            for stream in (process.stdin, process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
    return subprocess.CompletedProcess(argv, process.returncode, stdout, stderr)
