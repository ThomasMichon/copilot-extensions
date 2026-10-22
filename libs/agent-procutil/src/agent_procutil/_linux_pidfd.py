"""Linux pidfd acquisition for Python builds without the optional os wrapper."""

from __future__ import annotations

import ctypes
import os


def open_pidfd(pid: int) -> int:
    native = getattr(os, "pidfd_open", None)
    if native is not None:
        return native(pid)
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        acquire = libc.pidfd_open
    except AttributeError as error:
        raise RuntimeError("native libc pidfd acquisition is unavailable") from error
    acquire.argtypes = [ctypes.c_int, ctypes.c_uint]
    acquire.restype = ctypes.c_int
    descriptor = acquire(pid, 0)
    if descriptor < 0:
        number = ctypes.get_errno()
        raise OSError(number, os.strerror(number))
    return descriptor
