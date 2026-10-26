"""Read-only out-of-context WinEvent hooks owned by the registering pump thread."""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import threading
from typing import Callable

from windows_observation import NativeBlocked

EventCallback = getattr(ct, "WINFUNCTYPE", ct.CFUNCTYPE)(
    None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD,
)


class WinEventHooks:
    def __init__(self, user: object, kernel: object, receive: Callable,
                 on_error: Callable[[Exception], None]) -> None:
        self.user, self.kernel, self.receive, self.on_error = user, kernel, receive, on_error
        self.owner = threading.get_ident()
        self.handles: list[int] = []
        self.count = 0
        self.callback = EventCallback(self._receive)
        signatures = (
            (user.SetWinEventHook, [wt.DWORD, wt.DWORD, wt.HMODULE,
                                   EventCallback, wt.DWORD, wt.DWORD, wt.DWORD], wt.HANDLE),
            (user.UnhookWinEvent, [wt.HANDLE], wt.BOOL),
            (user.PeekMessageW, [ct.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT], wt.BOOL),
            (user.TranslateMessage, [ct.POINTER(wt.MSG)], wt.BOOL),
            (user.DispatchMessageW, [ct.POINTER(wt.MSG)], ct.c_ssize_t),
            (kernel.GetTickCount64, [], ct.c_ulonglong),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def _receive(self, _hook: int, event: int, hwnd: int, object_id: int,
                 child_id: int, source_thread: int, generated_ms: int) -> None:
        try:
            delay = (int(self.kernel.GetTickCount64()) - generated_ms) & 0xffffffff
            self.receive(event, int(hwnd or 0), object_id, child_id, source_thread, generated_ms, delay)
        except Exception as exc:
            self.on_error(exc)

    def _owner(self) -> None:
        if threading.get_ident() != self.owner:
            raise NativeBlocked("hook-thread-mismatch", "hooks must stay on their registering pump thread")

    def start(self) -> None:
        self._owner()
        for first, last in ((3, 3), (0x8000, 0x8003)):
            # OUTOFCONTEXT is zero. Do not skip this process or this thread.
            handle = self.user.SetWinEventHook(first, last, None, self.callback, 0, 0, 0)
            if not handle:
                raise NativeBlocked("event-hook-failed", f"SetWinEventHook: WinError {ct.get_last_error()}")
            self.handles.append(handle)
        self.count = len(self.handles)

    def drain(self) -> None:
        self._owner()
        message = wt.MSG()
        for _ in range(4096):
            if not self.user.PeekMessageW(ct.byref(message), None, 0, 0, 1):
                return
            if message.message == 0x12:
                raise NativeBlocked("hook-pump-quit", "observer received WM_QUIT")
            self.user.TranslateMessage(ct.byref(message))
            self.user.DispatchMessageW(ct.byref(message))
        raise NativeBlocked("hook-pump-overflow", "message queue exceeded the bounded drain")

    def close(self) -> None:
        self._owner()
        failures = []
        try:
            self.drain()
        except Exception as exc:
            failures.append(f"{type(exc).__name__}: {exc}")
        for handle in tuple(self.handles):
            if not self.user.UnhookWinEvent(handle):
                failures.append(f"UnhookWinEvent: WinError {ct.get_last_error()}")
            else:
                self.handles.remove(handle)
        if failures:
            raise NativeBlocked("event-hook-cleanup-failed", "; ".join(failures))
