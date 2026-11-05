"""Linux subreaper ownership and PID-reuse-safe process references."""

from __future__ import annotations

import ctypes
import errno
import logging
import os
import select
import signal
import sys
import time
from dataclasses import dataclass
from functools import lru_cache
from pathlib import Path
from typing import Protocol

from .diagnostics import process_start_time
from .singleton_state import ProcessIdentity

log = logging.getLogger("zdd")


@lru_cache(maxsize=1)
def _pidfd_libc() -> ctypes.CDLL:
    libc = ctypes.CDLL(None, use_errno=True)
    if not hasattr(libc, "pidfd_open") or not hasattr(libc, "pidfd_send_signal"):
        raise NotImplementedError("singleton manager requires native Linux pidfd primitives")
    libc.pidfd_open.argtypes = [ctypes.c_int, ctypes.c_uint]
    libc.pidfd_open.restype = ctypes.c_int
    libc.pidfd_send_signal.argtypes = [
        ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint,
    ]
    libc.pidfd_send_signal.restype = ctypes.c_int
    return libc


def _open_pidfd(pid: int) -> int:
    if hasattr(os, "pidfd_open"):
        return os.pidfd_open(pid)
    fd = _pidfd_libc().pidfd_open(pid, 0)
    if fd < 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))
    try:
        os.set_inheritable(fd, False)
    except BaseException:
        os.close(fd)
        raise
    return fd


def _send_pidfd_signal(fd: int, number: int) -> None:
    if hasattr(signal, "pidfd_send_signal"):
        signal.pidfd_send_signal(fd, number)
    elif _pidfd_libc().pidfd_send_signal(fd, number, None, 0) != 0:
        code = ctypes.get_errno()
        raise OSError(code, os.strerror(code))


class WatchedProcess(Protocol):
    identity: ProcessIdentity

    def alive(self) -> bool: ...
    def close(self) -> None: ...


def _pidfd_alive(fd: int) -> bool:
    poller = select.poll()
    poller.register(fd, select.POLLIN)
    events = poller.poll(0)
    if any(flags & select.POLLNVAL for _, flags in events):
        raise OSError(errno.EBADF, "invalid process pidfd")
    return not events


@dataclass
class ProcessReference:
    identity: ProcessIdentity
    fd: int

    def alive(self) -> bool:
        return _pidfd_alive(self.fd)

    def send_signal(self, number: int) -> bool:
        try:
            _send_pidfd_signal(self.fd, number)
        except ProcessLookupError:
            return False
        return True

    def close(self) -> None:
        os.close(self.fd)


def _process_stat(pid: int) -> tuple[str, int] | None:
    try:
        stat = Path(f"/proc/{pid}/stat").read_text(encoding="utf-8")
    except FileNotFoundError:
        return None
    suffix = stat[stat.rfind(")") + 1:].split()
    if len(suffix) < 2:
        raise ValueError(f"malformed process stat for pid {pid}")
    return suffix[0], int(suffix[1])


def _parent_pid(pid: int) -> int | None:
    stat = _process_stat(pid)
    return stat[1] if stat is not None else None


def _threads_stopped(pid: int) -> bool:
    task_dir = Path(f"/proc/{pid}/task")
    try:
        tids = {int(entry.name) for entry in task_dir.iterdir() if entry.name.isdigit()}
        if not tids:
            return False
        for tid in tids:
            stat = _process_stat(tid)
            if stat is None or stat[0] not in ("T", "t"):
                return False
        return tids == {int(entry.name) for entry in task_dir.iterdir() if entry.name.isdigit()}
    except FileNotFoundError:
        return False


class LinuxBackend:
    def __init__(self) -> None:
        if sys.platform != "linux":
            raise NotImplementedError("singleton manager is currently Linux-only")
        probe = _open_pidfd(os.getpid())
        try:
            _send_pidfd_signal(probe, 0)
        finally:
            os.close(probe)
        self.manager_pid = os.getpid()
        self.boot_id = Path("/proc/sys/kernel/random/boot_id").read_text().strip()
        owner = self.identify(self.manager_pid)
        if owner is None:
            raise RuntimeError("cannot establish manager process identity")
        self.owner = owner

    def identify(self, pid: int) -> ProcessIdentity | None:
        token = process_start_time(pid)
        return ProcessIdentity(pid, token, self.boot_id) if token is not None else None

    def pid_is_alive(self, pid: int) -> bool:
        """An identity-free refusal probe; never authority to adopt or signal."""
        if type(pid) is not int or pid <= 0:
            raise ValueError("liveness probe requires a positive integer pid")
        try:
            fd = _open_pidfd(pid)
        except ProcessLookupError:
            return False
        try:
            return _pidfd_alive(fd)
        finally:
            os.close(fd)

    def claim_tree(self) -> None:
        libc = ctypes.CDLL(None, use_errno=True)
        libc.prctl.argtypes = [
            ctypes.c_int, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong, ctypes.c_ulong,
        ]
        libc.prctl.restype = ctypes.c_int
        if libc.prctl(36, 1, 0, 0, 0) != 0:  # PR_SET_CHILD_SUBREAPER
            code = ctypes.get_errno()
            raise OSError(code, os.strerror(code))

    def open_process(self, identity: ProcessIdentity) -> ProcessReference | None:
        if identity.boot_id != self.boot_id or self.identify(identity.pid) != identity:
            return None
        try:
            fd = _open_pidfd(identity.pid)
        except ProcessLookupError:
            return None
        reference = ProcessReference(identity, fd)
        try:
            if self.identify(identity.pid) == identity and reference.alive():
                return reference
        except BaseException:
            reference.close()
            raise
        reference.close()
        return None

    def owns(self, reference: WatchedProcess, deadline: float | None = None) -> bool:
        if reference.identity.pid == self.manager_pid or not reference.alive():
            return False
        current = reference.identity.pid
        seen: set[int] = set()
        ancestry: list[tuple[ProcessIdentity, int]] = []
        # A caller with its own bounded budget (e.g. cleanup()'s shared cleanup
        # deadline) threads it through here so this scan can't independently
        # run its own full 5s past that budget; a standalone caller with no
        # deadline of its own still gets the default 5s ancestry bound.
        effective_deadline = deadline if deadline is not None else time.monotonic() + 5.0
        while True:
            if time.monotonic() >= effective_deadline:
                raise TimeoutError("ancestry ownership could not be verified before its deadline")
            if current == self.manager_pid:
                return reference.alive() and all(
                    self.identify(identity.pid) == identity
                    and _parent_pid(identity.pid) == parent
                    for identity, parent in ancestry
                )
            if current <= 1 or current in seen:
                return False
            seen.add(current)
            identity = self.identify(current)
            stat = _process_stat(current)
            if identity is None or stat is None:
                return False
            ancestry.append((identity, stat[1]))
            current = stat[1]

    def reap_zombies(self, watched_pid: int | None = None) -> None:
        children = Path(
            f"/proc/{self.manager_pid}/task/{self.manager_pid}/children",
        ).read_text().split()
        for raw_pid in children:
            pid = int(raw_pid)
            if pid == watched_pid:
                continue
            stat = _process_stat(pid)
            if stat is not None and stat == ("Z", self.manager_pid):
                try:
                    os.waitpid(pid, os.WNOHANG)
                except ChildProcessError:
                    pass  # Another completed child may already have been waited.

    def _wait_stopped(self, reference: ProcessReference, deadline: float) -> None:
        while reference.alive():
            if _threads_stopped(reference.identity.pid):
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("descendant did not stop before cleanup deadline")
            time.sleep(0.001)

    def cleanup(self, timeout: float = 5.0) -> None:
        """Freeze to a bounded fixed point, then kill only held descendant pidfds."""
        deadline = time.monotonic() + timeout
        frozen: dict[ProcessIdentity, ProcessReference] = {}
        try:
            while True:
                added = False
                for entry in Path("/proc").iterdir():
                    # Checked per entry (not only after a full pass) so a
                    # slow or large scan -- including one that adds nothing --
                    # can't run past the requested cleanup deadline before
                    # this raises.
                    if time.monotonic() >= deadline:
                        raise TimeoutError(
                            "descendant tree did not quiesce before cleanup deadline",
                        )
                    if not entry.name.isdigit() or int(entry.name) == self.manager_pid:
                        continue
                    identity = self.identify(int(entry.name))
                    if identity is None or identity in frozen:
                        continue
                    reference = self.open_process(identity)
                    if reference is None:
                        continue
                    try:
                        owned = self.owns(reference, deadline)
                    except BaseException:
                        reference.close()
                        raise
                    if owned:
                        frozen[identity] = reference
                        reference.send_signal(signal.SIGSTOP)
                        self._wait_stopped(reference, deadline)
                        added = True
                    else:
                        reference.close()
                if not added:
                    break
            for reference in frozen.values():
                reference.send_signal(signal.SIGKILL)
            while any(reference.alive() for reference in frozen.values()):
                self.reap_zombies()
                if time.monotonic() >= deadline:
                    raise TimeoutError("descendants did not exit before cleanup deadline")
                time.sleep(0.01)
            self.reap_zombies()
        finally:
            cleanup_error: OSError | None = None
            for reference in frozen.values():
                try:
                    if reference.alive():
                        reference.send_signal(signal.SIGKILL)
                except OSError as exc:
                    log.exception("Could not terminate owned descendant pid %d", reference.identity.pid)
                    cleanup_error = cleanup_error or exc
                try:
                    reference.close()
                except OSError as exc:
                    log.exception("Could not close owned pidfd for pid %d", reference.identity.pid)
                    cleanup_error = cleanup_error or exc
            if cleanup_error is not None:
                raise cleanup_error
