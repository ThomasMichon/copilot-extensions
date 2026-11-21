"""Selected-only, process-scoped asynchronous Windows wait-chain metadata."""

from __future__ import annotations

import time
from typing import Any


Row = dict[str, int | bool | None]
MAX_THREADS = 16
MAX_NODES = 16


class _BudgetExpired(Exception):
    pass


class WindowsWaitProbe:
    """Keep at most one thread and asynchronous WCT request in flight."""

    def __init__(self) -> None:
        import ctypes as ct
        from ctypes import wintypes as wt

        self.ct: Any = ct
        self.wt: Any = wt
        library = getattr(ct, "WinDLL")
        self.kernel: Any = library("kernel32", use_last_error=True)
        self.advapi: Any = library("advapi32", use_last_error=True)
        self.request: _Request | None = None

        class Thread(ct.Structure):
            _fields_ = [
                ("exit_status", wt.DWORD), ("teb", ct.c_void_p),
                ("pid", wt.DWORD), ("tid", wt.DWORD), ("affinity", ct.c_size_t),
                ("priority", ct.c_int), ("base_priority", ct.c_int),
                ("syscall_argument", ct.c_void_p), ("syscall", wt.WORD),
                ("created", wt.FILETIME), ("exited", wt.FILETIME),
                ("kernel", wt.FILETIME), ("user", wt.FILETIME),
                ("start_address", ct.c_void_p), ("captured", wt.FILETIME),
                ("flags", ct.c_int), ("suspend_count", wt.WORD),
                ("context_size", wt.WORD), ("context", ct.c_void_p),
            ]

        class Lock(ct.Structure):
            _fields_ = [
                ("name", wt.WCHAR * 128), ("timeout", ct.c_longlong),
                ("alertable", wt.BOOL),
            ]

        class WaitThread(ct.Structure):
            _fields_ = [
                ("pid", wt.DWORD), ("tid", wt.DWORD),
                ("wait_ms", wt.DWORD), ("switches", wt.DWORD),
            ]

        class Detail(ct.Union):
            _fields_ = [("lock", Lock), ("thread", WaitThread)]

        class Node(ct.Structure):
            _fields_ = [("kind", ct.c_int), ("status", ct.c_int), ("detail", Detail)]

        self.Thread: Any = Thread
        self.Node: Any = Node
        self.Callback: Any = getattr(ct, "WINFUNCTYPE")(
            None, wt.HANDLE, ct.c_size_t, wt.DWORD,
            ct.POINTER(wt.DWORD), ct.POINTER(Node), ct.POINTER(wt.BOOL),
        )
        signatures: dict[str, tuple[list[Any], Any]] = {
            "PssCaptureSnapshot": ([wt.HANDLE, ct.c_int, wt.DWORD, ct.POINTER(wt.HANDLE)], wt.DWORD),
            "PssWalkMarkerCreate": ([ct.c_void_p, ct.POINTER(wt.HANDLE)], wt.DWORD),
            "PssWalkSnapshot": ([wt.HANDLE, ct.c_int, wt.HANDLE, ct.c_void_p, wt.DWORD], wt.DWORD),
            "PssWalkMarkerFree": ([wt.HANDLE], wt.DWORD),
            "PssFreeSnapshot": ([wt.HANDLE, wt.HANDLE], wt.DWORD),
            "GetCurrentProcess": ([], wt.HANDLE),
            "OpenThread": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            "GetProcessIdOfThread": ([wt.HANDLE], wt.DWORD),
            "GetThreadTimes": ([wt.HANDLE] + [ct.POINTER(wt.FILETIME)] * 4, wt.BOOL),
            "WaitForSingleObject": ([wt.HANDLE, wt.DWORD], wt.DWORD),
            "CloseHandle": ([wt.HANDLE], wt.BOOL),
        }
        for name, (arguments, result) in signatures.items():
            function = getattr(self.kernel, name)
            function.argtypes, function.restype = arguments, result
        self.advapi.OpenThreadWaitChainSession.argtypes = [wt.DWORD, self.Callback]
        self.advapi.OpenThreadWaitChainSession.restype = wt.HANDLE
        self.advapi.GetThreadWaitChain.argtypes = [
            wt.HANDLE, ct.c_size_t, wt.DWORD, wt.DWORD,
            ct.POINTER(wt.DWORD), ct.POINTER(Node), ct.POINTER(wt.BOOL),
        ]
        self.advapi.GetThreadWaitChain.restype = wt.BOOL
        self.advapi.CloseThreadWaitChainSession.argtypes = [wt.HANDLE]
        self.advapi.CloseThreadWaitChainSession.restype = None

    @property
    def pending(self) -> bool:
        return self.request is not None

    def poll(self, target: tuple[int, int, int, int] | None, deadline: float) -> Row | None:
        """Collect a completed request or start one, with no event wait or retry."""
        if self.request is not None:
            if not self.request.done or time.monotonic() >= deadline:
                return None
            return self.finish()
        if target is None or time.monotonic() >= deadline:
            return None
        pid, handle, birth, cursor = target
        request = _Request(self, pid, birth)
        self.request = request
        try:
            request.start(handle, cursor, deadline)
        except _BudgetExpired:
            request.row["budget_exhausted"] = True
            request.done = True
        except OSError as error:
            request.row["error_code"] = int(error.errno or 0)
            request.done = True
        if request.done:
            return self.finish()
        return None

    def finish(self) -> Row | None:
        """Cancel/settle WCT before releasing its callback, buffers or thread pin."""
        request = self.request
        if request is None:
            return None
        request.close()
        self.request = None
        return request.row


class _Request:
    def __init__(self, owner: WindowsWaitProbe, pid: int, birth: int) -> None:
        self.owner, self.pid = owner, pid
        self.row: Row = {"pid": pid, "birth_100ns": birth, "stage": 0}
        self.snapshot, self.marker = owner.wt.HANDLE(), owner.wt.HANDLE()
        self.thread = 0
        self.session = 0
        self.callback: Any = None
        self.buffers: tuple[Any, Any, Any] | None = None
        self.done = False
        self.query_issued = False
        self.callback_received = False

    def check(self, deadline: float, stage: int) -> None:
        if time.monotonic() >= deadline:
            raise _BudgetExpired
        self.row["stage"] = stage

    @staticmethod
    def require(code: int) -> None:
        if code:
            raise OSError(int(code), "native wait observation unavailable")

    def last_error(self) -> int:
        return int(self.owner.ct.get_last_error())

    def start(self, handle: int, cursor: int, deadline: float) -> None:
        kernel = self.owner.kernel
        ct, wt = self.owner.ct, self.owner.wt
        self.check(deadline, 1)
        if kernel.WaitForSingleObject(handle, 0) != 258:
            self.row["process_not_live"] = True
            self.done = True
            return
        self.check(deadline, 2)
        self.require(kernel.PssCaptureSnapshot(handle, 0x80, 0, ct.byref(self.snapshot)))
        self.check(deadline, 3)
        self.require(kernel.PssWalkMarkerCreate(None, ct.byref(self.marker)))
        entries: list[tuple[int, int]] = []
        suspended = 0
        for _ in range(MAX_THREADS + 1):
            self.check(deadline, 4)
            entry = self.owner.Thread()
            status = kernel.PssWalkSnapshot(
                self.snapshot, 3, self.marker, ct.byref(entry), ct.sizeof(entry),
            )
            if status == 259:
                break
            self.require(status)
            if entry.pid != self.pid:
                raise OSError(13, "snapshot identity unavailable")
            birth = (int(entry.created.dwHighDateTime) << 32) | int(entry.created.dwLowDateTime)
            if len(entries) < MAX_THREADS:
                suspended += int(entry.suspend_count > 0)
            entries.append((int(entry.tid), birth))
        self.row.update({
            "threads_observed": min(len(entries), MAX_THREADS),
            "threads_truncated": len(entries) > MAX_THREADS, "suspended_threads": suspended,
        })
        self.free_snapshot()
        if not entries:
            self.done = True
            return
        tid, birth = entries[cursor % min(len(entries), MAX_THREADS)]
        self.check(deadline, 5)
        self.thread = int(kernel.OpenThread(0x100000 | 0x800, False, tid) or 0)
        if not self.thread:
            self.require(self.last_error() or 6)
        self.check(deadline, 6)
        times = [wt.FILETIME() for _ in range(4)]
        if not kernel.GetThreadTimes(self.thread, *(ct.byref(value) for value in times)):
            self.require(self.last_error() or 6)
        actual_birth = (int(times[0].dwHighDateTime) << 32) | int(times[0].dwLowDateTime)
        self.check(deadline, 7)
        if kernel.GetProcessIdOfThread(self.thread) != self.pid or actual_birth != birth or birth <= 0:
            raise OSError(13, "thread identity unavailable")
        self.check(deadline, 8)
        if kernel.WaitForSingleObject(self.thread, 0) != 258:
            raise OSError(6, "thread no longer live")
        self.callback = self.owner.Callback(self.receive)
        self.check(deadline, 9)
        self.session = int(self.owner.advapi.OpenThreadWaitChainSession(1, self.callback) or 0)
        if not self.session:
            self.require(self.last_error() or 6)
        self.buffers = wt.DWORD(MAX_NODES), (self.owner.Node * MAX_NODES)(), wt.BOOL()
        count, nodes, cycle = self.buffers
        self.check(deadline, 10)
        self.query_issued = True
        if not self.owner.advapi.GetThreadWaitChain(
            self.session, 0, 0, tid, ct.byref(count), nodes, ct.byref(cycle),
        ):
            error = self.last_error()
            if error != 997:
                self.require(error or 6)

    def receive(
        self, _session: int, _context: int, status: int, count: Any,
        nodes: Any, cycle: Any,
    ) -> None:
        try:
            row: Row = {"wait_status": int(status)}
            if status in (0, 234, 565) and count and nodes:
                size = int(count[0])
                row["nodes_truncated"] = size > MAX_NODES
                row["foreign_nodes_omitted"] = 0
                for index in range(min(size, MAX_NODES)):
                    node = nodes[index]
                    kind, state = int(node.kind), int(node.status)
                    if kind == 8 and int(node.detail.thread.pid) != self.pid:
                        row["foreign_nodes_omitted"] = int(row["foreign_nodes_omitted"] or 0) + 1
                        continue
                    if 1 <= kind < 13 and 1 <= state <= 10:
                        row[f"kind_{kind}"] = int(row.get(f"kind_{kind}") or 0) + 1
                        row[f"state_{state}"] = int(row.get(f"state_{state}") or 0) + 1
                    else:
                        row["unknown_category"] = True
                row["cycle"] = bool(cycle and cycle[0])
            self.row.update(row)
        except Exception:
            self.row["callback_error"] = True
        finally:
            self.callback_received = True
            self.done = True

    def free_snapshot(self) -> None:
        if self.marker:
            self.require(self.owner.kernel.PssWalkMarkerFree(self.marker))
            self.marker.value = None
        if self.snapshot:
            self.require(self.owner.kernel.PssFreeSnapshot(
                self.owner.kernel.GetCurrentProcess(), self.snapshot,
            ))
            self.snapshot.value = None

    def close(self) -> None:
        if self.session:
            self.owner.advapi.CloseThreadWaitChainSession(self.session)
            self.session = 0
            self.row["query_issued"] = self.query_issued
            self.row["callback_received"] = self.callback_received
            self.row["cancelled_pending"] = self.query_issued and not self.callback_received
        self.callback = None
        self.buffers = None
        if self.thread:
            if not self.owner.kernel.CloseHandle(self.thread):
                raise OSError("native wait thread closure remains unconfirmed")
            self.thread = 0
        self.free_snapshot()
