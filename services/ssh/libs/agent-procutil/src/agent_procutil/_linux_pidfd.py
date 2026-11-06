"""Linux pidfd operations for Python builds without the optional OS wrappers."""

from __future__ import annotations

import ctypes
import os
import signal


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


def send_pidfd_signal(descriptor: int, number: int) -> None:
    native = getattr(signal, "pidfd_send_signal", None)
    if native is not None:
        native(descriptor, number)
        return
    libc = ctypes.CDLL(None, use_errno=True)
    try:
        send = libc.pidfd_send_signal
    except AttributeError as error:
        raise RuntimeError("native libc pidfd signaling is unavailable") from error
    send.argtypes = [ctypes.c_int, ctypes.c_int, ctypes.c_void_p, ctypes.c_uint]
    send.restype = ctypes.c_int
    if send(descriptor, number, None, 0) < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
