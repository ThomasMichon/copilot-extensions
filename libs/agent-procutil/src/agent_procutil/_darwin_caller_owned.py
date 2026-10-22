"""Darwin kernel observations for the finite caller-owned process group."""

from __future__ import annotations

import ctypes
import errno
import os
import select
import sys


class _SigInfo(ctypes.Structure):
    # Darwin's 64-bit siginfo_t; fixed-width longs also permit ABI tests on Windows.
    _fields_ = [
        *((name, ctypes.c_int32) for name in ("si_signo", "si_errno", "si_code", "si_pid")),
        ("si_uid", ctypes.c_uint32), ("si_status", ctypes.c_int32),
        ("si_addr", ctypes.c_uint64), ("si_value", ctypes.c_uint64),
        ("si_band", ctypes.c_int64), ("padding", ctypes.c_uint64 * 7),
    ]


def _waitid():
    if sys.platform != "darwin" or ctypes.sizeof(ctypes.c_void_p) != 8:
        raise RuntimeError("Darwin child custody requires native 64-bit macOS")
    query = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True).waitid
    query.argtypes = [
        ctypes.c_int, ctypes.c_uint32, ctypes.POINTER(_SigInfo), ctypes.c_int,
    ]
    query.restype = ctypes.c_int
    return query


def observe_unreaped(process):
    """Use native waitid on macOS, including Python versions not exposing it."""
    data = _SigInfo()
    # sys/wait.h: P_PID=1, WEXITED=4, WNOHANG=1, WNOWAIT=32.
    if _waitid()(1, process.pid, ctypes.byref(data), 4 | 1 | 32) != 0:
        raise RuntimeError("native child custody is unconfirmed") from OSError(
            ctypes.get_errno(), "Darwin waitid failed",
        )
    if data.si_pid == 0:
        return None
    if data.si_pid != process.pid:
        raise RuntimeError("native child identity changed")
    return data


class OwnerWatch:
    """Register the still-live direct parent before any product can start."""

    def __init__(self, owner: int, *, parent: bool = True) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("Darwin process observation requires macOS")
        self.owner = owner
        self.queue = select.kqueue()
        try:
            if owner <= 1 or (parent and os.getppid() != owner):
                raise RuntimeError("native caller identity changed")
            event = select.kevent(
                owner, filter=select.KQ_FILTER_PROC,
                flags=select.KQ_EV_ADD | select.KQ_EV_ONESHOT,
                fflags=select.KQ_NOTE_EXIT,
            )
            events = self.queue.control([event], 1, 0)
            # Reparenting cannot be undone by PID reuse. Together these checks
            # bind the registration to the exact parent that spawned this guard.
            if events or (parent and os.getppid() != owner):
                raise RuntimeError("native caller is no longer live")
        except BaseException:
            self.close()
            raise

    def wait(self) -> None:
        events = self.queue.control(None, 1, None)
        if len(events) != 1:
            raise RuntimeError("native caller exit observation failed")
        event = events[0]
        if (
            event.ident != self.owner or event.filter != select.KQ_FILTER_PROC
            or event.flags & select.KQ_EV_ERROR
            or not event.fflags & select.KQ_NOTE_EXIT
        ):
            raise RuntimeError("native caller exit observation failed")

    def close(self) -> None:
        self.queue.close()


class _BSDInfo(ctypes.Structure):
    # sys/proc_info.h: PROC_PIDTBSDINFO (3), including native 64-bit alignment.
    _fields_ = [
        *((name, ctypes.c_uint32) for name in (
            "flags", "status", "xstatus", "pid", "ppid", "uid", "gid",
            "ruid", "rgid", "svuid", "svgid", "reserved",
        )),
        ("comm", ctypes.c_char * 16), ("name", ctypes.c_char * 32),
        *((name, ctypes.c_uint32) for name in (
            "nfiles", "pgid", "pjobc", "tdev", "tpgid",
        )),
        ("nice", ctypes.c_int32),
        ("start_sec", ctypes.c_uint64), ("start_usec", ctypes.c_uint64),
    ]


class GroupCensus:
    """Observe only the group whose original leader the caller keeps unreaped."""

    def __init__(self) -> None:
        if sys.platform != "darwin":
            raise RuntimeError("Darwin process observation requires macOS")
        library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
        self.list_pids = library.proc_listpgrppids
        self.list_pids.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_int]
        self.list_pids.restype = ctypes.c_int
        self.info = library.proc_pidinfo
        self.info.argtypes = [
            ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int,
        ]
        self.info.restype = ctypes.c_int

    def active(self, leader: int) -> bool:
        capacity = 64
        while capacity <= 1048576:
            pids = (ctypes.c_int * capacity)()
            ctypes.set_errno(0)
            # libproc.c's proc_listpgrppids returns a PID count, not the
            # byte count returned by the underlying proc_listpids API.
            count = self.list_pids(leader, pids, ctypes.sizeof(pids))
            if count < 0 or (count == 0 and ctypes.get_errno()):
                raise OSError(ctypes.get_errno(), "native process group census failed")
            if count > capacity:
                raise RuntimeError("native process group census is malformed")
            if count < capacity:
                break
            capacity *= 2
        else:
            raise RuntimeError("native process group census exceeds its bound")
        for pid in pids[:count]:
            if pid <= 0:
                raise RuntimeError("native process group identity is malformed")
            if pid == leader:
                continue
            data = _BSDInfo()
            ctypes.set_errno(0)
            returned = self.info(pid, 3, 0, ctypes.byref(data), ctypes.sizeof(data))
            if returned == 0 and ctypes.get_errno() == errno.ESRCH:
                continue
            if returned != ctypes.sizeof(data) or data.pid != pid:
                raise RuntimeError("native process group member identity is unavailable")
            # No member is signalled by numeric PID. The unreaped leader pins
            # the group; a recycled member PID outside that group is irrelevant.
            if data.pgid == leader and data.status != 5:  # SZOMB
                return True
        return False


def require_support() -> None:
    _waitid()
    with_owner = OwnerWatch(os.getpid(), parent=False)
    try:
        GroupCensus().active(os.getpgrp())
    finally:
        with_owner.close()
