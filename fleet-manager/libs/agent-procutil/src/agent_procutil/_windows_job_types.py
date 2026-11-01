"""Native Job accounting layout, loaded only by a native operation."""

from __future__ import annotations

import threading

_type_lock = threading.Lock()


def __getattr__(name: str):
    if name != "_JobBasicAccountingInformation":
        raise AttributeError(name)
    with _type_lock:
        if name not in globals():
            import ctypes

            class _JobBasicAccountingInformation(ctypes.Structure):
                _fields_ = [
                    ("TotalUserTime", ctypes.c_int64),
                    ("TotalKernelTime", ctypes.c_int64),
                    ("ThisPeriodTotalUserTime", ctypes.c_int64),
                    ("ThisPeriodTotalKernelTime", ctypes.c_int64),
                    ("TotalPageFaultCount", ctypes.c_uint32),
                    ("TotalProcesses", ctypes.c_uint32),
                    ("ActiveProcesses", ctypes.c_uint32),
                    ("TotalTerminatedProcesses", ctypes.c_uint32),
                ]

            globals()[name] = _JobBasicAccountingInformation
        return globals()[name]
