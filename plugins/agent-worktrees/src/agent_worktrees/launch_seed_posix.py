"""Exec-replace a native shell's child, with a finite backend receipt observer."""

from __future__ import annotations

import os
from pathlib import Path
import select
import signal
import stat
import time

from . import launch_seed_native as native, locks
from .launch_execution import Correlation


def _completion_file(record: Path, path: Path) -> int:
    from .session_shell import _plain

    if (
        not path.is_absolute()
        or not path.resolve().is_relative_to(record.parent.resolve() / "launch-attempts")
        or not path.name.startswith(".native-seed-exec.")
        or not path.parent.name.endswith(".shell")
    ):
        raise ValueError("Exec completion must belong to the copied launch assets")
    _plain(path)
    descriptor = os.open(path, os.O_WRONLY | os.O_NOFOLLOW)
    info = os.fstat(descriptor)
    if (
        not stat.S_ISREG(info.st_mode) or info.st_nlink != 1
        or info.st_uid != os.getuid() or info.st_size != 0
    ):
        os.close(descriptor)
        raise ValueError("Invalid exec completion file")
    return descriptor


def _outcome(descriptor: int) -> bytes:
    deadline = time.monotonic() + 10
    data = b""
    while True:
        remaining = deadline - time.monotonic()
        if remaining <= 0 or not select.select([descriptor], [], [], remaining)[0]:
            raise TimeoutError("Exec observation timed out")
        chunk = os.read(descriptor, 16)
        if not chunk:
            return data
        data += chunk
        if len(data) > 2:
            raise ValueError("Invalid exec observation")


def _observe(
    descriptor: int, completion: int, record: Path, correlation: Correlation,
    operation_id: str, pid: int, birth: str,
) -> None:
    status = 3
    try:
        # Stay in the caller's containment group, but never consume the
        # foreground backend's terminal interrupt as observer cancellation.
        signal.signal(signal.SIGINT, signal.SIG_IGN)
        signal.signal(signal.SIGQUIT, signal.SIG_IGN)
        def timed_out(_signal, _frame):
            signal.signal(signal.SIGALRM, signal.SIG_DFL)
            signal.alarm(1)
            raise TimeoutError("Exec observation timed out")
        signal.signal(signal.SIGALRM, timed_out)
        signal.alarm(10)
        null = os.open(os.devnull, os.O_RDWR)
        try:
            for target in (0, 1, 2):
                os.dup2(null, target)
        finally:
            os.close(null)
        outcome = _outcome(descriptor)
        if outcome == b"R":
            native.spawned(record, correlation, operation_id, pid=pid, process_start_time=birth)
            status = 0
        elif outcome == b"RF":
            native.spawn_failed(record, correlation, operation_id)
        else:
            raise ValueError("Exec completion is unconfirmed")
    except (*native._ERRORS, KeyboardInterrupt):
        try:
            native.interrupted(record, correlation, operation_id)
        except native._ERRORS:
            pass
    finally:
        os.close(descriptor)
        try:
            os.ftruncate(completion, 0)
            os.write(completion, f"{status}\n".encode("ascii"))
        finally:
            os.close(completion)


def execute(
    record: Path, correlation: Correlation, operation_id: str,
    command: list[str], completion_file: Path,
) -> int:
    """Keep the caller's direct child PID; never wait for the backend in Python.

    The close-on-exec error pipe follows the POSIX subprocess spawn convention.
    A missing/failed observer or a backend gone before live identity validation
    is unknown, never permission to restore or replay the claimed prompt.
    """
    if os.name != "posix" or not command or not command[0] or not all(
        isinstance(argument, str) and "\0" not in argument for argument in command
    ):
        raise ValueError("POSIX exec requires a literal backend command")
    _execution, saved = native._load(record, correlation, operation_id)
    if any(event["phase"] == "process-spawned" for event in saved["events"]):
        raise ValueError("Backend spawn was already observed; do not replay it")
    pid = os.getpid()
    birth = locks.process_start_time(pid)
    if not birth:
        raise ValueError("Exec child birth is unconfirmed")
    native._require_process(pid, birth)
    completion = _completion_file(record, completion_file)
    try:
        reader, writer = os.pipe()  # Non-inheritable on both Linux and macOS.
    except OSError:
        os.close(completion)
        native.spawn_failed(record, correlation, operation_id)
        raise
    try:
        intermediary = os.fork()
    except OSError:
        os.close(reader)
        os.close(writer)
        os.close(completion)
        native.spawn_failed(record, correlation, operation_id)
        raise
    if intermediary == 0:
        os.close(writer)
        try:
            observer = os.fork()
            if observer == 0:
                try:
                    _observe(reader, completion, record, correlation, operation_id, pid, birth)
                finally:
                    os._exit(0)
            os._exit(0)
        except BaseException:
            os._exit(3)
    os.close(reader)
    os.close(completion)
    outcome = b"U"
    previous_signals = {}
    interrupt_pending = False
    cleaning = False

    def defer_interrupt(_signal, _frame):
        nonlocal interrupt_pending, cleaning
        interrupt_pending = True
        if not cleaning:
            cleaning = True
            raise KeyboardInterrupt

    interrupt_handler = signal.getsignal(signal.SIGINT)
    if interrupt_handler != signal.SIG_IGN:
        signal.signal(signal.SIGINT, defer_interrupt)
    try:
        # Reap only the immediate fork intermediary, not the backend. Its child
        # is reparented and cannot become an unowned zombie under Copilot.
        while True:
            try:
                _child, status = os.waitpid(intermediary, 0)
                break
            except InterruptedError:
                continue
        if status != 0:
            cleaning = True
            native.spawn_failed(record, correlation, operation_id)
            return 3
        if interrupt_pending:
            raise KeyboardInterrupt
        os.write(writer, b"R")
        try:
            # Match Popen(restore_signals=True), not CPython's ignored defaults.
            for name in ("SIGPIPE", "SIGXFZ", "SIGXFSZ"):
                signum = getattr(signal, name, None)
                if signum is not None:
                    previous_signals[signum] = signal.getsignal(signum)
                    signal.signal(signum, signal.SIG_DFL)
            if interrupt_pending:
                raise KeyboardInterrupt
            os.execvpe(command[0], command, os.environ)
        except OSError:
            cleaning = True
            outcome = b"F"
            return 3
    except BaseException:
        cleaning = True
        raise
    finally:
        cleaning = True
        # Exec resets the caught SIGINT handler in the kernel. On unwind,
        # defer it until non-success is published and all descriptors settle.
        try:
            try:
                os.write(writer, outcome)
            finally:
                os.close(writer)
        finally:
            try:
                for signum, handler in previous_signals.items():
                    signal.signal(signum, handler)
            finally:
                signal.signal(signal.SIGINT, interrupt_handler)
        if interrupt_pending:
            raise KeyboardInterrupt
