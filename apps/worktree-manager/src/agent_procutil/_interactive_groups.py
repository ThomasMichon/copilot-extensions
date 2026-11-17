"""Kernel-scoped retirement of every group in a private completion session."""

from __future__ import annotations

import ctypes
import errno
import os
import signal
import sys
import time
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

_ANCHOR = """
import os,signal
for name in ('SIGHUP','SIGINT','SIGQUIT','SIGTSTP','SIGTTIN','SIGTTOU'):
    signal.signal(getattr(signal,name),signal.SIG_IGN)
os.read(0,1)
os.killpg(os.getpgrp(),signal.SIGKILL)
"""


class SessionCensus:
    def __init__(self, session: int):
        if type(session) is not int or session <= 1:
            raise ValueError("interactive session identity is invalid")
        self.session = session
        if sys.platform == "darwin":
            from ._darwin_caller_owned import _BSDInfo

            self.info_type = _BSDInfo
            library = ctypes.CDLL("/usr/lib/libproc.dylib", use_errno=True)
            self.list_pids = library.proc_listallpids
            self.list_pids.argtypes = [ctypes.c_void_p, ctypes.c_int]
            self.list_pids.restype = ctypes.c_int
            self.info = library.proc_pidinfo
            self.info.argtypes = [
                ctypes.c_int, ctypes.c_int, ctypes.c_uint64, ctypes.c_void_p, ctypes.c_int,
            ]
            self.info.restype = ctypes.c_int
        elif not sys.platform.startswith("linux"):
            raise RuntimeError("interactive session observation requires Linux or macOS")

    def _darwin_pids(self):
        capacity = 256
        while capacity <= 1048576:
            values = (ctypes.c_int * capacity)()
            ctypes.set_errno(0)
            count = self.list_pids(values, ctypes.sizeof(values))
            if count <= 0:
                raise OSError(ctypes.get_errno(), "native session inventory failed")
            if count > capacity:
                raise RuntimeError("native session inventory is malformed")
            if count < capacity:
                return list(values[:count])
            capacity *= 2
        raise RuntimeError("native session inventory exceeds its bound")

    def groups(self, *, exclude_pid: int | None = None) -> set[int]:
        groups = set()
        if sys.platform == "darwin":
            pids = self._darwin_pids()
            if os.getpid() not in pids:
                raise RuntimeError("native session inventory omitted its observer")
            for pid in pids:
                if pid <= 0 or pid == exclude_pid:
                    continue
                try:
                    if os.getsid(pid) != self.session:
                        continue
                    data = self.info_type()
                    ctypes.set_errno(0)
                    returned = self.info(pid, 3, 0, ctypes.byref(data), ctypes.sizeof(data))
                    if returned == 0 and ctypes.get_errno() == errno.ESRCH:
                        continue
                    if returned != ctypes.sizeof(data) or data.pid != pid:
                        raise RuntimeError("native session member observation is unavailable")
                    if os.getsid(pid) == self.session and data.status != 5:
                        groups.add(int(data.pgid))
                except ProcessLookupError:
                    continue
            if any(group <= 1 for group in groups):
                raise RuntimeError("native session group identity is malformed")
            return groups
        with os.scandir("/proc") as entries:
            pids = [int(entry.name) for entry in entries if entry.name.isdigit()]
        if os.getpid() not in pids:
            raise RuntimeError("native session inventory omitted its observer")
        for pid in pids:
            if pid == exclude_pid:
                continue
            try:
                fields = Path(f"/proc/{pid}/stat").read_bytes().rpartition(b")")[2].split()
                if len(fields) < 20:
                    raise RuntimeError("native session member observation is malformed")
                if int(fields[3]) == self.session and fields[0] != b"Z":
                    groups.add(int(fields[2]))
            except (FileNotFoundError, ProcessLookupError):
                continue
        if any(group <= 1 for group in groups):
            raise RuntimeError("native session group identity is malformed")
        return groups


@dataclass
class _Anchor:
    pid: int
    returncode: int | None = None


class SessionCustody:
    """Runs inside the private session; direct unreaped children pin group IDs."""

    def __init__(self, lifetime: int):
        self.session = os.getpid()
        if os.getsid(0) != self.session or os.getpgrp() != self.session:
            raise RuntimeError("interactive guardian must own its private session")
        self.lifetime = lifetime
        self.census = SessionCensus(self.session)
        self.census.groups()
        self.anchors: dict[int, _Anchor] = {}
        self.pending: list[_Anchor] = []

    def _anchor(self, group: int, deadline: float) -> bool:
        from .owned_process import _observe_unreaped

        if group in self.anchors:
            _observe_unreaped(self.anchors[group])
            return True
        if time.monotonic() >= deadline:
            raise TimeoutError("interactive group anchor exceeded its deadline")
        try:
            # setpgroup is checked by the kernel before exec. A recycled
            # group in another session cannot accept this owned anchor.
            pid = os.posix_spawn(
                sys.executable, [sys.executable, "-I", "-S", "-c", _ANCHOR],
                os.environ, setpgroup=group,
                file_actions=[
                    (os.POSIX_SPAWN_DUP2, self.lifetime, 0),
                    (os.POSIX_SPAWN_OPEN, 1, os.devnull, os.O_WRONLY, 0o600),
                    (os.POSIX_SPAWN_OPEN, 2, os.devnull, os.O_WRONLY, 0o600),
                ],
            )
        except OSError as error:
            if error.errno in (errno.ESRCH, errno.EPERM):
                return False
            raise
        anchor = _Anchor(pid)
        self.pending.append(anchor)
        status = _observe_unreaped(anchor)
        if status is not None:
            return False
        try:
            if os.getsid(pid) != self.session or os.getpgid(pid) != group:
                raise RuntimeError("interactive group custody is unconfirmed")
        except ProcessLookupError:
            return False
        except PermissionError:
            if sys.platform == "darwin" and _observe_unreaped(anchor) is not None:
                return False
            raise
        self.anchors[group] = anchor
        return True

    def _kill(self, group: int) -> None:
        try:
            os.killpg(group, signal.SIGKILL)
        except ProcessLookupError:
            pass
        except PermissionError:
            if sys.platform != "darwin" or group in self.census.groups():
                raise

    def retire(self, timeout: float = 2.0) -> None:
        deadline = time.monotonic() + timeout
        empty = 0
        while True:
            groups = self.census.groups(exclude_pid=os.getpid()) - {self.session}
            empty = 0 if groups else empty + 1
            if empty == 2:
                return
            if time.monotonic() >= deadline:
                raise TimeoutError("interactive session still has live job groups")
            for group in groups:
                if self._anchor(group, deadline):
                    self._kill(group)
            time.sleep(.005)


def confirm_session(
    process, timeout: float = 2.0, *, on_wait: Callable[[], None] | None = None,
) -> None:
    from .owned_process import OwnedCleanupError, _observe_unreaped

    deadline = time.monotonic() + timeout
    try:
        while _observe_unreaped(process) is None:
            if on_wait is not None:
                on_wait()
            if time.monotonic() >= deadline:
                raise TimeoutError("interactive guardian has not settled")
            time.sleep(.005)
        census = SessionCensus(process.pid)
        empty = 0
        while empty < 2:
            if on_wait is not None:
                on_wait()
            empty = 0 if census.groups() else empty + 1
            if time.monotonic() >= deadline:
                raise TimeoutError("interactive session still has live members")
            time.sleep(.005)
        process.wait(timeout=2)
    except BaseException as error:
        raise OwnedCleanupError(process) from error
