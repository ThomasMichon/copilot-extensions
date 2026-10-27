"""Non-spawning Darwin process-group counts and resident bytes (stdlib only).

ABI: Apple's xnu bsd/sys/proc_info{,_private}.h and
libsyscall/wrappers/libproc/libproc{.h,.c}. The caller retains PGID custody;
these observations neither establish ownership nor signal any process.
"""
from __future__ import annotations

import ctypes
import errno
import sys
import time
from typing import NamedTuple, TypeVar

MAX_PIDS = 65536
MAX_PASSES = 3
_Structure = TypeVar("_Structure", bound=ctypes.Structure)


class _ShortBSDInfo(ctypes.Structure):
    _fields_ = [
        *((name, ctypes.c_uint32) for name in ("pid", "ppid", "pgid", "status")),
        ("comm", ctypes.c_char * 16),
        *((name, ctypes.c_uint32) for name in (
            "flags", "uid", "gid", "ruid", "rgid", "svuid", "svgid", "reserved",
        )),
    ]


class _UniqueInfo(ctypes.Structure):
    _fields_ = [
        ("uuid", ctypes.c_ubyte * 16),
        ("uniqueid", ctypes.c_uint64), ("parent_uniqueid", ctypes.c_uint64),
        ("idversion", ctypes.c_int32), ("parent_version", ctypes.c_int32),
        ("reserved2", ctypes.c_uint64), ("reserved3", ctypes.c_uint64),
    ]


class _TaskInfo(ctypes.Structure):
    _fields_ = [
        *((name, ctypes.c_uint64) for name in (
            "virtual_size", "resident_size", "total_user", "total_system",
            "threads_user", "threads_system",
        )),
        *((name, ctypes.c_int32) for name in (
            "policy", "faults", "pageins", "cow_faults", "messages_sent",
            "messages_received", "syscalls_mach", "syscalls_unix", "csw",
            "threadnum", "numrunning", "priority",
        )),
    ]


class _Member(NamedTuple):
    pid: int
    pgid: int
    status: int
    incarnation: tuple[int, int, bytes]


class _CensusChanged(ValueError):
    """Discard every row from this pass and obtain a fresh complete census."""

    def __init__(self, reason: str, *, pid: int = 0, flavor: int = 0,
                 phase: str, native_errno: int = 0, observed_pgid: int = 0) -> None:
        self.reason = reason
        self.pid = pid
        self.flavor = flavor
        self.phase = phase
        self.native_errno = native_errno
        self.observed_pgid = observed_pgid
        super().__init__(
            f"{reason} pid={pid} flavor={flavor} phase={phase} "
            f"errno={native_errno} observed_pgid={observed_pgid}"
        )


class GroupObservationChanged(ValueError):
    """Known census churn exhausted one bounded observation, not an empty group."""

    def __init__(self, last: _CensusChanged) -> None:
        self.last = last
        self.pid, self.flavor, self.phase = last.pid, last.flavor, last.phase
        self.native_errno = last.native_errno
        super().__init__(f"Darwin process-group observation: group churn exceeded; last={last}")


class _ObservationDeadline(TimeoutError):
    """The caller's current sampling cycle has no observation budget left."""


def _invalid(cause: str) -> ValueError:
    return ValueError(f"Darwin process-group observation: {cause}")


class _Native:
    def __init__(self) -> None:
        self.deadline: float | None = None
        if sys.platform != "darwin" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise OSError(errno.ENOTSUP, "Darwin group observation requires native 64-bit macOS")
        if (ctypes.sizeof(ctypes.c_int) != 4 or ctypes.sizeof(_ShortBSDInfo) != 64
                or _ShortBSDInfo.pgid.offset != 8 or _ShortBSDInfo.uid.offset != 36
                or ctypes.sizeof(_UniqueInfo) != 56 or _UniqueInfo.uniqueid.offset != 16
                or _UniqueInfo.idversion.offset != 32 or ctypes.sizeof(_TaskInfo) != 96
                or _TaskInfo.resident_size.offset != 8):
            raise _invalid("unsupported ABI")
        try:
            library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            self.listpids = library.proc_listpids
            self.listpids.argtypes = [
                ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int,
            ]
            self.listpids.restype = ctypes.c_int
            self.pidinfo = library.proc_pidinfo
            self.pidinfo.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int,
            ]
            self.pidinfo.restype = ctypes.c_int
        except (AttributeError, OSError):
            raise OSError(errno.ENOTSUP, "Darwin native group inspector unavailable") from None

    def _check_deadline(self) -> None:
        if self.deadline is not None and time.monotonic() >= self.deadline:
            raise _ObservationDeadline(errno.ETIMEDOUT, "Darwin group observation cycle expired")

    def inventory(self, pgid: int) -> set[int]:
        capacity = 64
        while True:
            data = (ctypes.c_int * capacity)()
            self._check_deadline()
            ctypes.set_errno(0)
            size = self.listpids(2, pgid, data, ctypes.sizeof(data))  # PROC_PGRP_ONLY.
            error = ctypes.get_errno()
            # proc_listpids returns bytes, mapping native failure to zero with
            # errno retained. Zero with cleared errno is an actual empty census.
            if size < 0 or (size == 0 and error):
                raise OSError(error or errno.EIO, "Darwin group inventory failed")
            if size > ctypes.sizeof(data) or size % ctypes.sizeof(ctypes.c_int):
                raise _invalid("invalid inventory size")
            values = list(data[:size // ctypes.sizeof(ctypes.c_int)])
            if any(pid <= 0 for pid in values) or len(set(values)) != len(values):
                raise _invalid("invalid inventory identities")
            self._check_deadline()
            if size < ctypes.sizeof(data):
                return set(values)
            if capacity >= MAX_PIDS:
                raise _invalid("inventory bound exceeded")
            capacity = min(capacity * 2, MAX_PIDS)

    def _read(
        self, pid: int, flavor: int, structure: type[_Structure], *, zombies: bool = False,
    ) -> _Structure:
        data = structure()
        self._check_deadline()
        ctypes.set_errno(0)
        size = self.pidinfo(pid, flavor, int(zombies), ctypes.byref(data), ctypes.sizeof(data))
        if size == 0:
            error = ctypes.get_errno()
            if error == errno.ESRCH:
                raise _CensusChanged("member disappeared", pid=pid, flavor=flavor,
                                     phase="pidinfo", native_errno=error)
            raise OSError(error or errno.EIO, "Darwin group member inspection failed")
        if size != ctypes.sizeof(data):
            raise _invalid("partial member inspection")
        self._check_deadline()
        return data

    def _unique(self, pid: int) -> tuple[int, int, bytes]:
        data = self._read(pid, 17, _UniqueInfo, zombies=True)
        if data.uniqueid == 0:
            raise _invalid("unverified process incarnation")
        return data.uniqueid, data.idversion, bytes(data.uuid)

    def member(self, pid: int, pgid: int) -> _Member:
        before = self._unique(pid)
        data = self._read(pid, 13, _ShortBSDInfo, zombies=True)
        after = self._unique(pid)
        if data.pid != pid or data.status not in (1, 2, 3, 4, 5, 6, 7):
            raise _invalid("invalid member identity")
        if before != after:
            raise _CensusChanged("member incarnation changed", pid=pid, flavor=17,
                                 phase="member-incarnation")
        if data.pgid != pgid:
            raise _CensusChanged("member group changed", pid=pid, flavor=13,
                                 phase="group-membership", observed_pgid=data.pgid)
        return _Member(pid, data.pgid, data.status, before)

    def rss(self, pid: int) -> int:
        return self._read(pid, 4, _TaskInfo).resident_size  # Native bytes, not KiB.


def group_usage(
    pgid: int, *, live_only: bool = False, deadline: float | None = None,
) -> tuple[int, int]:
    """Return a complete group count and resident bytes, or refuse visibly.

    Zombies count against the ordinary process budget with zero resident bytes.
    Live-only cleanup excludes zombies, but never excludes an unreadable member.
    """
    if type(pgid) is not int or not 1 <= pgid <= 0x7fffffff:
        raise _invalid("invalid process-group identifier")
    native = _Native()
    native.deadline = deadline
    last_change: _CensusChanged | None = None
    for _ in range(MAX_PASSES):
        try:
            pids = native.inventory(pgid)
            count, resident = 0, 0
            for pid in sorted(pids):
                before = native.member(pid, pgid)
                rss = native.rss(pid) if before.status != 5 else 0
                after = native.member(pid, pgid)
                if before.incarnation != after.incarnation:
                    raise _CensusChanged("member incarnation changed around RSS", pid=pid,
                                         flavor=4, phase="rss-incarnation")
                if before.status == 5 and after.status != 5:
                    raise _invalid("terminal member became live")
                if after.status != 5:
                    count += 1
                    resident += rss
                elif not live_only:
                    count += 1
            final_pids = native.inventory(pgid)
            if final_pids != pids:
                raise _CensusChanged("group inventory changed",
                                     pid=min(final_pids ^ pids), phase="inventory-after")
            return count, resident
        except _CensusChanged as change:
            last_change = change
            continue
        except _ObservationDeadline:
            if last_change is not None:
                raise GroupObservationChanged(last_change) from last_change
            raise
    assert last_change is not None
    raise GroupObservationChanged(last_change) from last_change
