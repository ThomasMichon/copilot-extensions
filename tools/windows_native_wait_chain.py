"""Read-only wait-chain diagnostics for the disposable native startup fixture."""

import ctypes
from ctypes import wintypes as w
import json
import sys


def describe(pids: set[int]) -> list[dict]:
    kernel = ctypes.WinDLL("kernel32", use_last_error=True)
    advapi = ctypes.WinDLL("advapi32", use_last_error=True)

    class ThreadEntry(ctypes.Structure):
        _fields_ = [
            ("size", w.DWORD), ("usage", w.DWORD), ("tid", w.DWORD), ("pid", w.DWORD),
            ("priority", w.LONG), ("delta", w.LONG), ("flags", w.DWORD),
        ]

    class Lock(ctypes.Structure):
        _fields_ = [("name", w.WCHAR * 128), ("timeout", ctypes.c_longlong), ("alertable", w.BOOL)]

    class Thread(ctypes.Structure):
        _fields_ = [
            ("pid", w.DWORD), ("tid", w.DWORD), ("wait", w.DWORD), ("switches", w.DWORD),
        ]

    class Details(ctypes.Union):
        _fields_ = [("lock", Lock), ("thread", Thread)]

    class Node(ctypes.Structure):
        _fields_ = [("type", w.DWORD), ("status", w.DWORD), ("details", Details)]

    kernel.CreateToolhelp32Snapshot.argtypes = [w.DWORD, w.DWORD]
    kernel.CreateToolhelp32Snapshot.restype = w.HANDLE
    for name in ("Thread32First", "Thread32Next"):
        operation = getattr(kernel, name)
        operation.argtypes = [w.HANDLE, ctypes.POINTER(ThreadEntry)]
        operation.restype = w.BOOL
    kernel.CloseHandle.argtypes = [w.HANDLE]
    kernel.CloseHandle.restype = w.BOOL
    advapi.OpenThreadWaitChainSession.argtypes = [w.DWORD, ctypes.c_void_p]
    advapi.OpenThreadWaitChainSession.restype = w.HANDLE
    advapi.CloseThreadWaitChainSession.argtypes = [w.HANDLE]
    advapi.GetThreadWaitChain.argtypes = [
        w.HANDLE, ctypes.c_size_t, w.DWORD, w.DWORD, ctypes.POINTER(w.DWORD),
        ctypes.POINTER(Node), ctypes.POINTER(w.BOOL),
    ]
    advapi.GetThreadWaitChain.restype = w.BOOL
    snapshot = kernel.CreateToolhelp32Snapshot(4, 0)
    if snapshot == ctypes.c_void_p(-1).value:
        raise ctypes.WinError(ctypes.get_last_error())
    try:
        session = advapi.OpenThreadWaitChainSession(0, None)
        if not session:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            result = []
            entry = ThreadEntry(size=ctypes.sizeof(ThreadEntry))
            found = kernel.Thread32First(snapshot, ctypes.byref(entry))
            while found:
                if entry.pid in pids:
                    nodes = (Node * 16)()
                    count, cycle = w.DWORD(16), w.BOOL()
                    success = advapi.GetThreadWaitChain(
                        session, 0, 7, entry.tid, ctypes.byref(count), nodes, ctypes.byref(cycle),
                    )
                    item = {"pid": entry.pid, "tid": entry.tid}
                    if not success:
                        item["error"] = ctypes.get_last_error()
                    else:
                        item["cycle"] = bool(cycle.value)
                        item["nodes"] = [
                            {"type": node.type, "status": node.status, **(
                                {"pid": node.details.thread.pid, "tid": node.details.thread.tid}
                                if node.type == 8 else {"name": node.details.lock.name}
                            )}
                            for node in nodes[:count.value]
                        ]
                    result.append(item)
                found = kernel.Thread32Next(snapshot, ctypes.byref(entry))
            error = ctypes.get_last_error()
            if error != 18:
                raise ctypes.WinError(error)
            return result
        finally:
            advapi.CloseThreadWaitChainSession(session)
    finally:
        if not kernel.CloseHandle(snapshot):
            raise ctypes.WinError(ctypes.get_last_error())


if __name__ == "__main__":
    print(json.dumps(describe({int(value) for value in sys.argv[1:]}), indent=2))
