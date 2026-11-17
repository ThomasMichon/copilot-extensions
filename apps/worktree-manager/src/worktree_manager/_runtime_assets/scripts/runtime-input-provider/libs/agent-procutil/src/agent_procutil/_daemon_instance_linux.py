"""Compiler-free Linux custody for an explicitly disposable daemon tree."""

from __future__ import annotations

import contextlib
from collections.abc import Callable
import ctypes
from dataclasses import dataclass
import errno
import os
from pathlib import Path
import select
import signal
import time

from . import _daemon_instance_store as store
from . import _daemon_instance_clock as lease_clock
from ._daemon_instance_identity import birth_identity, path_identity
from ._linux_pidfd import open_pidfd, send_pidfd_signal
from .daemon_instances import DaemonInstanceReceipt


@dataclass
class _Lease:
    hard_mono: float
    lease_mono: float


def _exited(descriptor: int) -> bool:
    observer = select.poll()
    observer.register(descriptor, select.POLLIN)
    events = observer.poll(0)
    if any(flags & (select.POLLERR | select.POLLNVAL) for _, flags in events):
        raise OSError("daemon pidfd observation failed")
    return any(flags & select.POLLIN for _, flags in events)


def check() -> None:
    descriptor = open_pidfd(os.getpid())
    try:
        send_pidfd_signal(descriptor, 0)
    finally:
        os.close(descriptor)
    if not all(hasattr(os, name) for name in (
        "waitid", "P_PID", "WEXITED", "WNOHANG", "WNOWAIT", "CLD_EXITED",
    )):
        raise RuntimeError("daemon supervision requires unreaped-child wait observation")


def _pin(pid: int, expected: str) -> int:
    descriptor = open_pidfd(pid)
    try:
        if birth_identity(pid) != expected or _exited(descriptor):
            raise OSError("daemon process identity changed")
    except BaseException:
        os.close(descriptor)
        raise
    return descriptor


def _subreaper() -> None:
    library = ctypes.CDLL(None, use_errno=True)
    configure = library.prctl
    configure.argtypes = [
        ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
    ]
    configure.restype = ctypes.c_int
    if configure(36, 1, 0, 0, 0):  # PR_SET_CHILD_SUBREAPER
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))


def _validate(record: dict[str, str]) -> tuple[float, float]:
    if record["purpose"] not in ("unattended-test", "interactive-test"):
        raise ValueError("Linux tree supervision requires a disposable instance")
    if record["state"] not in ("reserved", "bound"):
        raise ValueError("daemon reservation already consumed")
    root = Path(record["runtime_root"])
    store._plain(root, directory=True, owned=False)
    if root.resolve(strict=True) != root or path_identity(root) != (
        record["runtime_device"], record["runtime_inode"],
    ):
        raise OSError("daemon runtime root changed")
    source = Path(record["source"])
    if not source.is_absolute() or not source.is_file():
        raise ValueError("daemon source provenance unavailable")
    if record["state"] == "bound":
        if (record["pid"], record["birth"]) != (
            str(os.getpid()), birth_identity(os.getpid()),
        ):
            raise ValueError("daemon admission process differs")
    else:
        launcher = int(record["launcher_pid"])
        if launcher not in (os.getpid(), os.getppid()) or (
            birth_identity(launcher) != record["launcher_birth"]
        ):
            raise ValueError("daemon launch belongs to a different process")
    deadline, hard = lease_clock.bounds(record)
    if time.monotonic() >= deadline:
        raise ValueError("daemon admission lease expired")
    return deadline, hard


def _retire_children() -> None:
    path = Path(f"/proc/self/task/{os.getpid()}/children")
    with path.open("rb") as stream:
        children = stream.read(store._MAX_RECORD + 1)
    if len(children) > store._MAX_RECORD:
        raise OSError("daemon descendant census exceeds its bound")
    for raw in children.split():
        if not raw.isdigit() or int(raw) <= 0:
            raise ValueError("invalid owned descendant")
        pid = int(raw)
        try:
            descriptor = open_pidfd(pid)
        except ProcessLookupError:
            continue
        try:
            # No wait/reap occurs during the census. Direct-parent custody pins
            # each child until the pidfd is acquired and its parent rechecked.
            fields = Path(f"/proc/{pid}/stat").read_bytes().rsplit(b") ", 1)[1].split()
            if int(fields[1]) != os.getpid():
                raise OSError("daemon descendant parent changed")
            if not _exited(descriptor):
                try:
                    send_pidfd_signal(descriptor, signal.SIGKILL)
                except ProcessLookupError:
                    pass
        finally:
            os.close(descriptor)


def _settle(child: int) -> bool:
    # The original child has not been reaped: its PID still pins this group.
    custody_valid = True
    try:
        os.killpg(child, signal.SIGKILL)
    except ProcessLookupError:
        pass
    except OSError as error:
        store._log.error("daemon group stop unconfirmed: %s", error)
        custody_valid = False
    end = time.monotonic() + 5
    while True:
        try:
            _retire_children()
        except (OSError, RuntimeError, ValueError, IndexError) as error:
            store._log.error("daemon descendant custody unavailable: %s", error)
            custody_valid = False
        while True:
            if time.monotonic() >= end:
                return False
            try:
                reaped, _status = os.waitpid(-1, os.WNOHANG)
            except ChildProcessError:
                return custody_valid
            except InterruptedError:
                continue
            if reaped == 0:
                break
        if time.monotonic() >= end:
            return False
        time.sleep(.01)


def _child(command: list[str], gate: int) -> None:
    try:
        descriptors = [
            int(entry.name) for entry in Path("/proc/self/fd").iterdir()
            if entry.name.isdigit() and int(entry.name) not in (0, 1, 2, gate)
        ]
        for descriptor in descriptors:
            try:
                os.close(descriptor)
            except OSError as error:
                if error.errno != errno.EBADF:
                    raise
        os.setpgid(0, 0)
        for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGPIPE):
            signal.signal(name, signal.SIG_DFL)
        if os.read(gate, 1) != b"\1":
            os._exit(125)
        os.close(gate)
        os.execvp(command[0], command)
    except BaseException as error:
        # No Python unwinding or inherited parent finally block after fork.
        with contextlib.suppress(OSError):
            os.write(2, f"daemon child execution failed: {error}\n".encode(errors="replace"))
        os._exit(127)


def _watch_child(
    receipt: DaemonInstanceReceipt, record: dict[str, str], child: int, owner: int,
    lease: _Lease, interrupted: Callable[[], bool],
) -> int:
    inspection_deferred = False
    while True:
        observed = os.waitid(os.P_PID, child, os.WEXITED | os.WNOHANG | os.WNOWAIT)
        if observed is not None:
            return observed.si_status if observed.si_code == os.CLD_EXITED else (
                128 + observed.si_status
            )
        if interrupted() or _exited(owner) or time.monotonic() >= lease.hard_mono:
            return 124
        try:
            with store._locked(receipt) as record_path:
                updated = store._read(record_path)
                if any(updated[key] != record[key] for key in (
                    "pid", "birth", "supervisor_pid", "supervisor_birth",
                    "owner_pid", "owner_birth", "hard_deadline_mono", "clock_domain",
                )):
                    raise ValueError("daemon supervision identity changed")
                renewed, hard = lease_clock.bounds(updated)
                if hard != lease.hard_mono:
                    raise ValueError("daemon lease exceeds hard maximum")
                lease.lease_mono = renewed
                if time.monotonic() >= lease.lease_mono:
                    return 124
                inspection_deferred = False
        except BlockingIOError:
            if not inspection_deferred:
                store._log.warning("daemon lease inspection deferred: inventory is locked")
            inspection_deferred = True
        if time.monotonic() >= lease.hard_mono:
            return 124
        time.sleep(.05)


def supervise(path: Path, command: list[str], *, ready_fd: int | None = None) -> int:
    """Own one fresh child group until its owner/lease ends and descendants settle."""
    receipt = DaemonInstanceReceipt(path.stem, path.parent)
    interrupted = False
    owner = None
    gate_read = gate_write = None
    child = None
    record = None
    handlers = {}
    result = 125

    def interrupt(_number, _frame):
        nonlocal interrupted
        interrupted = True

    try:
        check()
        with store._locked(receipt) as record_path:
            if path != record_path or not command:
                raise ValueError("invalid daemon supervision invocation")
            record = store._read(record_path)
            deadline, hard = _validate(record)
            lease = _Lease(hard, deadline)
            owner = _pin(int(record["owner_pid"]), record["owner_birth"])
            if os.getpgrp() != os.getpid():
                os.setpgid(0, 0)
            _subreaper()
            gate_read, gate_write = os.pipe2(os.O_CLOEXEC)
            for name in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP, signal.SIGCHLD):
                handlers[name] = signal.signal(
                    name, signal.SIG_DFL if name == signal.SIGCHLD else interrupt,
                )
            child = os.fork()
            if child == 0:
                # The lock descriptor is close-on-exec but also must not be
                # retained while the parent publishes and releases admission.
                _child(command, gate_read)
            os.close(gate_read)
            gate_read = None
            os.setpgid(child, child)
            record.update(
                pid=str(child), birth=birth_identity(child),
                supervisor_pid=str(os.getpid()), supervisor_birth=birth_identity(os.getpid()),
                state="running",
            )
            store._write(record_path, record)
        if ready_fd is not None:
            message = f"ready:{child}\n".encode("ascii")
            if os.write(ready_fd, message) != len(message):
                raise OSError("daemon admission notification incomplete")
            os.close(ready_fd)
            ready_fd = None
        os.write(gate_write, b"\1")
        os.close(gate_write)
        gate_write = None
        result = _watch_child(receipt, record, child, owner, lease, lambda: interrupted)
    except (OSError, ValueError, KeyError, RuntimeError) as error:
        store._log.error("daemon supervision refused: %s", error)
        result = 125
    finally:
        if child is not None and child > 0:
            if not _settle(child):
                store._log.error("daemon descendant settlement unconfirmed")
                result = 125
            elif record is not None and record.get("supervisor_pid") == str(os.getpid()):
                try:
                    store._write(path.with_suffix(".settled"), {**record, "tree_settled": "1"})
                except (OSError, ValueError) as error:
                    store._log.error("daemon settlement publication failed: %s", error)
                    result = 125
        for descriptor in (owner, gate_read, gate_write, ready_fd):
            if descriptor is not None:
                try:
                    os.close(descriptor)
                except OSError as error:
                    store._log.error("daemon descriptor close unconfirmed: %s", error)
                    result = 125
        for name, handler in handlers.items():
            signal.signal(name, handler)
    return result
