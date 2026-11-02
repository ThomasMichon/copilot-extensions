"""Bounded, read-only current-user Darwin process inspection (stdlib only).

ABI references: Apple's xnu bsd/sys/{proc_info,proc_info_private,sysctl}.h,
libsyscall/wrappers/libproc/{libproc.h,libproc.c}, bsd/kern/kern_sysctl.c
(sysctl_procargsx), and bsd/kern/kern_exec.c (exec_extract_strings).
No shell output, process signals or environment are exposed.
"""
from __future__ import annotations

import ctypes
import errno
import os
import struct
import sys
from typing import Any

MAX_PIDS = 65536
MAX_ARG_BYTES = 8 * 1024 * 1024
MAX_ARGC = 65536
MAX_CENSUS_PASSES = 3
PATH_BYTES = 4096  # PROC_PIDPATHINFO_MAXSIZE = 4 * MAXPATHLEN.


class _BSDInfo(ctypes.Structure):
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


class _Info(ctypes.Structure):
    _fields_ = [("bsd", _BSDInfo), ("unique", _UniqueInfo)]


class _ReadRefused(RuntimeError):
    """PID-bound failure evidence, never authority to return a partial census."""

    def __init__(self, pid: int, phase: str, native_errno: int, detail: str,
                 evidence: dict[str, int]) -> None:
        super().__init__(detail)
        self.pid = pid
        self.phase = phase
        self.native_errno = native_errno
        self.evidence = evidence


def _refuse(scope: str, pid: int | None = None) -> RuntimeError:
    suffix = "" if pid is None else f" PID {pid}"
    return RuntimeError(f"Darwin process inspection: {scope}{suffix}")


def _parse_procargs(data: bytes, word_size: int) -> list[str]:
    """Consume only argc strings; bytes after those strings are never decoded."""
    if not 6 <= len(data) <= MAX_ARG_BYTES + 4:
        raise _refuse("invalid argument size")
    argc = struct.unpack_from("=i", data)[0]
    if not 1 <= argc <= MAX_ARGC:
        raise _refuse("invalid argument count")
    end = data.find(b"\0", 4)
    if end <= 4:
        raise _refuse("invalid executable string")
    if word_size not in (4, 8):
        raise _refuse("invalid argument ABI")
    # kern_exec.c exec_extract_strings aligns the saved path to the target
    # pointer width. sysctl strips the 16-byte "executable_path=" prefix,
    # which does not alter that alignment. Skipping *all* NULs instead would
    # consume empty argv[0] and could accidentally return environment bytes.
    path_size = end - 4 + 1
    cursor = 4 + (path_size + word_size - 1) // word_size * word_size
    if cursor > len(data) or any(data[end + 1:cursor]):
        raise _refuse("invalid argument alignment")
    result = []
    for _ in range(argc):
        end = data.find(b"\0", cursor)
        if end < cursor:
            raise _refuse("truncated arguments")
        result.append(data[cursor:end].decode("utf-8", "surrogateescape"))
        cursor = end + 1
    return result


class _Native:
    """Closed native API seam; tests substitute CDLL, never a runtime provider."""

    def __init__(self) -> None:
        if sys.platform != "darwin" or ctypes.sizeof(ctypes.c_void_p) != 8:
            raise _refuse("requires native 64-bit macOS")
        if (ctypes.sizeof(ctypes.c_int) != 4 or ctypes.sizeof(_BSDInfo) != 136
                or ctypes.sizeof(_UniqueInfo) != 56 or ctypes.sizeof(_Info) != 192
                or _BSDInfo.start_sec.offset != 120 or _Info.unique.offset != 136):
            raise _refuse("unsupported ABI")
        try:
            proc = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            system = ctypes.CDLL("/usr/lib/libSystem.B.dylib", use_errno=True)
            self.listpids = proc.proc_listpids
            self.listpids.argtypes = [
                ctypes.c_uint32, ctypes.c_uint32, ctypes.c_void_p, ctypes.c_int,
            ]
            self.listpids.restype = ctypes.c_int
            self.pidinfo = proc.proc_pidinfo
            self.pidinfo.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int,
            ]
            self.pidinfo.restype = ctypes.c_int
            self.pidpath = proc.proc_pidpath
            self.pidpath.argtypes = [ctypes.c_int, ctypes.c_void_p, ctypes.c_uint32]
            self.pidpath.restype = ctypes.c_int
            self.sysctl = system.sysctl
            self.sysctl.argtypes = [
                ctypes.POINTER(ctypes.c_int), ctypes.c_uint, ctypes.c_void_p,
                ctypes.POINTER(ctypes.c_size_t), ctypes.c_void_p, ctypes.c_size_t,
            ]
            self.sysctl.restype = ctypes.c_int
        except (AttributeError, OSError):
            raise _refuse("native inspector unavailable") from None
        self.uid = os.getuid()
        if self.uid != os.geteuid():
            raise _refuse("ambiguous caller ownership")
        argmax = ctypes.c_int()
        self._sysctl((1, 8), argmax, ctypes.sizeof(argmax))
        if not 1 <= argmax.value <= MAX_ARG_BYTES:
            raise _refuse("invalid kernel argument bound")
        self.arg_bytes = argmax.value + 4

    def _sysctl(self, keys: tuple[int, ...], buffer: Any, capacity: int) -> int:
        mib = (ctypes.c_int * len(keys))(*keys)
        size = ctypes.c_size_t(capacity)
        ctypes.set_errno(0)
        result = self.sysctl(mib, len(keys), ctypes.byref(buffer),
                             ctypes.byref(size), None, 0)
        if result != 0:
            raise OSError(ctypes.get_errno(), "Darwin process sysctl failed")
        if not 0 < size.value <= capacity or (len(keys) == 2 and size.value != capacity):
            raise _refuse("partial sysctl result")
        return size.value

    def inventory(self) -> set[int]:
        # Include effective and real UID ownership; privileged candidates refuse
        # below rather than disappearing from the consumer census.
        pids = self._inventory_kind(4) | self._inventory_kind(5)
        if len(pids) > MAX_PIDS or os.getpid() not in pids:
            raise _refuse("incomplete current-user inventory")
        return pids

    def _inventory_kind(self, kind: int) -> set[int]:
        capacity = 256
        while True:
            buffer = (ctypes.c_int * capacity)()
            ctypes.set_errno(0)
            size = self.listpids(kind, self.uid, buffer, ctypes.sizeof(buffer))
            if size <= 0:
                raise _refuse("current-user inventory unavailable")
            if size > ctypes.sizeof(buffer) or size % 4:
                raise _refuse("invalid inventory size")
            values = list(buffer[:size // 4])
            if any(pid <= 0 for pid in values) or len(set(values)) != len(values):
                raise _refuse("invalid inventory identities")
            if size < ctypes.sizeof(buffer):
                return set(values)
            if capacity >= MAX_PIDS:
                raise _refuse("inventory bound exceeded")
            capacity = min(capacity * 2, MAX_PIDS)

    def info(self, pid: int) -> _Info:
        data = _Info()
        ctypes.set_errno(0)
        size = self.pidinfo(pid, 18, 0, ctypes.byref(data), ctypes.sizeof(data))
        if size == 0:
            raise OSError(ctypes.get_errno(), "Darwin process identity unavailable")
        if size != ctypes.sizeof(data):
            raise _refuse("partial identity", pid)
        bsd = data.bsd
        if (bsd.pid != pid or bsd.ppid > 0x7fffffff or bsd.start_sec == 0
                or bsd.start_usec >= 1000000 or data.unique.uniqueid == 0
                or bsd.status not in (1, 2, 3, 4, 5, 6, 7)):
            raise _refuse("invalid identity", pid)
        if (bsd.uid, bsd.ruid, bsd.svuid) != (self.uid,) * 3:
            raise _refuse("unverified current-user ownership", pid)
        return data

    def image(self, pid: int) -> str:
        # Nonzero fill ensures an omitted terminator cannot look like success.
        data = ctypes.create_string_buffer(b"\xff" * PATH_BYTES, PATH_BYTES)
        ctypes.set_errno(0)
        size = self.pidpath(pid, data, PATH_BYTES)
        if size <= 0:
            raise OSError(ctypes.get_errno(), "Darwin executable inspection failed")
        raw = data.raw
        if not 0 < size < PATH_BYTES or raw.find(b"\0") != size or raw[0] != 47:
            raise _refuse("invalid executable result", pid)
        return raw[:size].decode("utf-8", "surrogateescape")

    def argv(self, pid: int, word_size: int) -> list[str]:
        mib = (ctypes.c_int * 3)(1, 49, pid)
        required = ctypes.c_size_t()
        ctypes.set_errno(0)
        if self.sysctl(mib, 3, None, ctypes.byref(required), None, 0) != 0:
            raise OSError(ctypes.get_errno(), "Darwin argument size query failed")
        if not 6 <= required.value <= self.arg_bytes:
            raise _refuse("invalid native argument size", pid)
        data = ctypes.create_string_buffer(self.arg_bytes)
        try:
            size = self._sysctl((1, 49, pid), data, self.arg_bytes)
            # XNU can silently copy the *tail* of its data for a small buffer.
            # A full bounded allocation plus the preflight avoids accepting it.
            if size > required.value:
                raise _refuse("argument size changed", pid)
            return _parse_procargs(data.raw[:size], word_size)
        finally:
            # The kernel also copies environment bytes. Do not retain that buffer.
            ctypes.memset(data, 0, self.arg_bytes)


def _identity(data: _Info) -> tuple[Any, ...]:
    bsd, unique = data.bsd, data.unique
    return (bsd.pid, bsd.ppid, bsd.uid, bsd.ruid, bsd.svuid, bsd.flags & 0x10,
            bsd.start_sec, bsd.start_usec, unique.uniqueid, unique.idversion,
            bytes(unique.uuid))


def _failure_evidence(native: _Native, pid: int) -> dict[str, int]:
    """Read bounded numeric evidence only; it cannot admit a process or retry."""
    evidence = {"census_uid": native.uid,
                "caller_ruid": os.getuid(), "caller_uid": os.geteuid()}
    # Each complete filtered inventory uses at most nine bounded native calls.
    # Unknown, empty, malformed or truncated inventories never prove absence.
    for kind, prefix in ((4, "uid_inventory"), (5, "ruid_inventory")):
        evidence.update({prefix + "_member": -1, prefix + "_self": -1,
                         prefix + "_errno": 0})
        ctypes.set_errno(0)
        try:
            pids = native._inventory_kind(kind)
        except (OSError, RuntimeError) as error:
            evidence[prefix + "_errno"] = (
                error.errno or 0 if isinstance(error, OSError) else ctypes.get_errno()
            )
        else:
            evidence[prefix + "_member"] = int(pid in pids)
            evidence[prefix + "_self"] = int(os.getpid() in pids)
    # Flavor 13 omits the same-effective-UID check applied to flavors 3/18.
    # MAC policy still applies to every flavor; neither read bypasses custody.
    for flavor, structure in ((3, _BSDInfo), (13, _ShortBSDInfo)):
        prefix = f"bsd{flavor}"
        data = structure()
        ctypes.set_errno(0)
        try:
            size = native.pidinfo(pid, flavor, 0, ctypes.byref(data), ctypes.sizeof(data))
        except OSError as error:
            size = 0
            ctypes.set_errno(error.errno or 0)
        evidence.update({prefix + "_bytes": size, prefix + "_errno": ctypes.get_errno()})
        valid = size == ctypes.sizeof(data) and data.pid == pid
        evidence[prefix + "_valid"] = int(valid)
        if valid:
            for name in ("uid", "ruid", "svuid", "flags", "status"):
                evidence[prefix + "_" + name] = int(getattr(data, name))
            if flavor == 13:
                evidence["bsd13_ppid"] = int(data.ppid)
                evidence["bsd13_pgid"] = int(data.pgid)
                evidence["bsd13_comm_is_ps"] = int(bytes(data.comm) == b"ps")
    return evidence


def _row(native: _Native, pid: int) -> dict[str, Any] | None:
    before: _Info | None = None
    phase = "identity-before"
    try:
        before = native.info(pid)
        if before.bsd.status == 5:  # SZOMB: explicit terminal evidence.
            return None
        phase = "image-before"
        image = native.image(pid)
        phase = "arguments"
        argv = native.argv(pid, 8 if before.bsd.flags & 0x10 else 4)  # PROC_FLAG_LP64.
        phase = "image-after"
        final_image = native.image(pid)
        phase = "identity-after"
        after = native.info(pid)
        if after.bsd.status == 5:
            return None
        if _identity(before) != _identity(after) or image != final_image:
            raise _refuse("process incarnation changed", pid)
        return {"pid": pid, "ppid": before.bsd.ppid, "image": image,
                "command": " ".join(argv), "argv": argv}
    except OSError as error:
        if error.errno == errno.ESRCH:
            return None
        if phase == "arguments":
            phase = ("argument-size" if error.strerror == "Darwin argument size query failed"
                     else "argument-read")
        # KERN_PROCARGS2 uses EINVAL for missing proc/task/stack, but also for
        # invalid inputs. Only an independent kernel observation can excuse it.
        confirmation, confirmation_errno, status, identity_matches = "unverified", 0, 0, -1
        try:
            observed = native.info(pid)
            status = observed.bsd.status
            if before is not None:
                identity_matches = int(_identity(before) == _identity(observed))
            confirmation = ("zombie" if status == 5 else
                            "changed" if identity_matches == 0 else "live")
        except OSError as confirmation_error:
            confirmation_errno = confirmation_error.errno or 0
            if confirmation_errno == errno.ESRCH:
                confirmation = "gone"
        except RuntimeError:
            pass
        if error.errno == errno.EINVAL and confirmation in {"gone", "zombie"}:
            return None
        # Observations after other failures are diagnostic only. In particular,
        # EPERM/EIO followed by ESRCH does not convert an unverified read to gone.
        evidence = _failure_evidence(native, pid)
        detail = (f"{_refuse('native read refused', pid)} phase={phase} errno={error.errno or 0} "
                  f"confirmation={confirmation} confirmation_errno={confirmation_errno} "
                  f"confirmation_status={status} confirmation_identity={identity_matches}")
        detail += " " + " ".join(f"{key}={value}" for key, value in evidence.items())
        raise _ReadRefused(pid, phase, error.errno or 0, detail, evidence) from None


def inspect_processes() -> list[dict[str, Any]]:
    """Inspect a bounded stable current-user census, including this inspector.

    This is an observation, not custody against future process creation. Caller
    transaction/lease guards must remain held through the uninstall operation.
    """
    native = _Native()
    for _ in range(MAX_CENSUS_PASSES):
        before = native.inventory()
        rows = [row for pid in sorted(before) if (row := _row(native, pid)) is not None]
        if native.inventory() != before:
            continue
        if not any(row["pid"] == os.getpid() for row in rows):
            raise _refuse("inspector missing from live census")
        if os.getuid() != native.uid or os.geteuid() != native.uid:
            raise _refuse("caller ownership changed")
        return rows
    raise _refuse("inventory churn exceeded")
