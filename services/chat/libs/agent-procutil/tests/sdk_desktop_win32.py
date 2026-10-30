"""Compiler-free desktop bindings; no global process census or title capture."""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
import os
import threading

from sdk_desktop_observer import ObservationBlocked


Callback = getattr(ct, "WINFUNCTYPE", ct.CFUNCTYPE)
EventCallback = Callback(
    None, wt.HANDLE, wt.DWORD, wt.HWND, wt.LONG, wt.LONG, wt.DWORD, wt.DWORD,
)
WindowCallback = Callback(wt.BOOL, wt.HWND, wt.LPARAM)


class ProcessPin:
    def __init__(self, api, pid: int, birth: int | None = None, *, owned: bool = False):
        self.api, self.pid, self.owned = api, pid, owned
        self.handle = api.kernel.OpenProcess(0x1000 | 0x100000 | (1 if owned else 0), False, pid)
        api.check(self.handle, "pin process")
        try:
            self.birth = api.birth(self.handle)
            if birth is not None and birth != self.birth:
                raise ObservationBlocked("process birth changed before pin admission")
        except BaseException:
            self.close()
            raise

    def gone(self) -> bool:
        state = self.api.kernel.WaitForSingleObject(self.handle, 0)
        if state not in (0, 0x102):
            raise ObservationBlocked(f"process wait is unobservable: {state}")
        return state == 0

    def terminate_owned(self) -> None:
        if not self.owned:
            raise ObservationBlocked("foreign process termination is not authorized")
        if not self.gone():
            terminated = self.api.kernel.TerminateProcess(self.handle, 125)
            error = ct.get_last_error() if not terminated else 0
            if not terminated and not self.gone():
                raise ObservationBlocked(f"retire owned fixture: WinError {error}")

    def close(self) -> None:
        if self.handle:
            handle, self.handle = self.handle, None
            self.api.check(self.api.kernel.CloseHandle(handle), "close pinned process")


class EventHooks:
    def __init__(self, api, receive, on_error):
        self.api, self.receive, self.on_error = api, receive, on_error
        self.owner = threading.get_ident()
        self.handles: list[int] = []
        self.count = 0
        self.callback = EventCallback(self._receive)

    def _receive(self, _hook, event, hwnd, object_id, child_id, source_thread, generated_ms):
        try:
            delay = (int(self.api.kernel.GetTickCount64()) - generated_ms) & 0xFFFFFFFF
            self.receive(event, int(hwnd or 0), object_id, child_id, source_thread, generated_ms, delay)
        except Exception as error:
            self.on_error(error)

    def owner_only(self) -> None:
        if threading.get_ident() != self.owner:
            raise ObservationBlocked("WinEvent operation left its registering pump thread")

    def start(self) -> None:
        self.owner_only()
        self.api.user.PeekMessageW(ct.byref(wt.MSG()), None, 0, 0, 0)
        for first, last in ((3, 3), (0x8000, 0x8003)):
            handle = self.api.user.SetWinEventHook(first, last, None, self.callback, 0, 0, 0)
            self.api.check(handle, "register unfiltered out-of-context WinEvent hook")
            self.handles.append(handle)
        self.count = len(self.handles)

    def drain(self) -> None:
        self.owner_only()
        self.api.pump()

    def close(self) -> None:
        self.owner_only()
        failures = []
        try:
            self.drain()
        except Exception as error:
            failures.append(str(error))
        for handle in tuple(self.handles):
            if self.api.user.UnhookWinEvent(handle):
                self.handles.remove(handle)
            else:
                failures.append(f"UnhookWinEvent: WinError {ct.get_last_error()}")
        if failures:
            raise ObservationBlocked("; ".join(failures))


class WinAPI:
    def __init__(self):
        if os.name != "nt":
            raise ObservationBlocked("native Windows desktop required")
        self.user = ct.WinDLL("user32", use_last_error=True)
        self.kernel = ct.WinDLL("kernel32", use_last_error=True)
        self.advapi = ct.WinDLL("advapi32", use_last_error=True)
        self._bind(self.kernel, {
            "GetCurrentProcess": ([], wt.HANDLE),
            "GetCurrentThreadId": ([], wt.DWORD),
            "GetTickCount64": ([], ct.c_ulonglong),
            "WTSGetActiveConsoleSessionId": ([], wt.DWORD),
            "OpenProcess": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            "CloseHandle": ([wt.HANDLE], wt.BOOL),
            "WaitForSingleObject": ([wt.HANDLE, wt.DWORD], wt.DWORD),
            "TerminateProcess": ([wt.HANDLE, wt.UINT], wt.BOOL),
            "ProcessIdToSessionId": ([wt.DWORD, ct.POINTER(wt.DWORD)], wt.BOOL),
            "IsProcessInJob": ([wt.HANDLE, wt.HANDLE, ct.POINTER(wt.BOOL)], wt.BOOL),
            "GetProcessTimes": ([wt.HANDLE] + [ct.POINTER(wt.FILETIME)] * 4, wt.BOOL),
            "PeekNamedPipe": ([wt.HANDLE, wt.LPVOID, wt.DWORD, ct.POINTER(wt.DWORD),
                               ct.POINTER(wt.DWORD), ct.POINTER(wt.DWORD)], wt.BOOL),
            "GetNamedPipeServerProcessId": ([wt.HANDLE, ct.POINTER(wt.ULONG)], wt.BOOL),
        })
        self._bind(self.advapi, {
            "OpenProcessToken": ([wt.HANDLE, wt.DWORD, ct.POINTER(wt.HANDLE)], wt.BOOL),
            "GetTokenInformation": (
                [wt.HANDLE, ct.c_int, wt.LPVOID, wt.DWORD, ct.POINTER(wt.DWORD)], wt.BOOL,
            ),
        })
        self._bind(self.user, {
            "GetForegroundWindow": ([], wt.HWND),
            "GetShellWindow": ([], wt.HWND),
            "GetProcessWindowStation": ([], wt.HANDLE),
            "GetThreadDesktop": ([wt.DWORD], wt.HANDLE),
            "OpenInputDesktop": ([wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            "CloseDesktop": ([wt.HANDLE], wt.BOOL),
            "GetUserObjectInformationW": (
                [wt.HANDLE, ct.c_int, wt.LPVOID, wt.DWORD, ct.POINTER(wt.DWORD)], wt.BOOL,
            ),
            "GetSystemMetrics": ([ct.c_int], ct.c_int),
            "IsWindow": ([wt.HWND], wt.BOOL),
            "IsWindowVisible": ([wt.HWND], wt.BOOL),
            "GetWindowThreadProcessId": ([wt.HWND, ct.POINTER(wt.DWORD)], wt.DWORD),
            "EnumWindows": ([WindowCallback, wt.LPARAM], wt.BOOL),
            "SetWinEventHook": (
                [wt.DWORD, wt.DWORD, wt.HMODULE, EventCallback,
                 wt.DWORD, wt.DWORD, wt.DWORD], wt.HANDLE,
            ),
            "UnhookWinEvent": ([wt.HANDLE], wt.BOOL),
            "PeekMessageW": ([ct.POINTER(wt.MSG), wt.HWND, wt.UINT, wt.UINT, wt.UINT], wt.BOOL),
            "TranslateMessage": ([ct.POINTER(wt.MSG)], wt.BOOL),
            "DispatchMessageW": ([ct.POINTER(wt.MSG)], ct.c_ssize_t),
            "CreateWindowExW": (
                [wt.DWORD, wt.LPCWSTR, wt.LPCWSTR, wt.DWORD,
                 ct.c_int, ct.c_int, ct.c_int, ct.c_int,
                 wt.HWND, wt.HMENU, wt.HINSTANCE, wt.LPVOID], wt.HWND,
            ),
            "SetForegroundWindow": ([wt.HWND], wt.BOOL),
            "DestroyWindow": ([wt.HWND], wt.BOOL),
        })

    @staticmethod
    def _bind(library, signatures) -> None:
        for name, (arguments, result) in signatures.items():
            function = getattr(library, name)
            function.argtypes, function.restype = arguments, result

    @staticmethod
    def check(result, operation: str) -> None:
        if not result:
            raise ObservationBlocked(f"{operation}: WinError {ct.get_last_error()}")

    def birth(self, handle: int) -> int:
        times = [wt.FILETIME() for _ in range(4)]
        self.check(self.kernel.GetProcessTimes(handle, *(ct.byref(t) for t in times)), "process birth")
        return (times[0].dwHighDateTime << 32) | times[0].dwLowDateTime

    def identity(self) -> dict:
        return {"pid": os.getpid(), "birth": self.birth(self.kernel.GetCurrentProcess())}

    def pin(self, pid: int, birth: int | None = None, *, owned: bool = False) -> ProcessPin:
        return ProcessPin(self, pid, birth, owned=owned)

    def require_parent_pipe(self, request: dict) -> ProcessPin:
        import msvcrt

        parent = ProcessPin(self, request["parent_pid"], request["parent_birth"])
        try:
            server = wt.ULONG()
            self.check(self.kernel.GetNamedPipeServerProcessId(
                msvcrt.get_osfhandle(0), ct.byref(server),
            ), "input pipe server identity")
            if server.value != parent.pid or parent.gone():
                raise ObservationBlocked("private input pipe does not name its original live caller")
            return parent
        except BaseException:
            parent.close()
            raise

    def pipe_bytes(self) -> int | None:
        import msvcrt

        available = wt.DWORD()
        if not self.kernel.PeekNamedPipe(
            msvcrt.get_osfhandle(0), None, 0, None, ct.byref(available), None,
        ):
            error = ct.get_last_error()
            if error == 109:
                return None
            raise ObservationBlocked(f"private command pipe: WinError {error}")
        return int(available.value)

    def session(self, pid: int) -> int:
        result = wt.DWORD()
        self.check(self.kernel.ProcessIdToSessionId(pid, ct.byref(result)), "process session")
        return int(result.value)

    def object_name(self, handle: int) -> str:
        name, size = ct.create_unicode_buffer(256), wt.DWORD()
        self.check(self.user.GetUserObjectInformationW(
            handle, 2, name, ct.sizeof(name), ct.byref(size),
        ), "window station/desktop name")
        return name.value

    def facts(self) -> dict:
        token, elevation, size = wt.HANDLE(), wt.DWORD(), wt.DWORD()
        self.check(self.advapi.OpenProcessToken(
            self.kernel.GetCurrentProcess(), 8, ct.byref(token),
        ), "read own token")
        try:
            self.check(self.advapi.GetTokenInformation(
                token, 20, ct.byref(elevation), ct.sizeof(elevation), ct.byref(size),
            ), "read elevation")
        finally:
            self.check(self.kernel.CloseHandle(token), "close own token")
        station = self.user.GetProcessWindowStation()
        self.check(station, "window station")
        flags = (wt.DWORD * 3)()
        self.check(self.user.GetUserObjectInformationW(
            station, 1, flags, ct.sizeof(flags), ct.byref(size),
        ), "window station visibility")
        desktop = self.user.OpenInputDesktop(0, False, 0x101)
        self.check(desktop, "read input desktop")
        try:
            input_name = self.object_name(desktop)
            thread_name = self.object_name(
                self.user.GetThreadDesktop(self.kernel.GetCurrentThreadId()),
            )
        finally:
            self.check(self.user.CloseDesktop(desktop), "close owned input-desktop handle")
        foreground, shell = self.foreground(), int(self.user.GetShellWindow() or 0)
        if not foreground or not shell:
            raise ObservationBlocked("shell or foreground window is unobservable")
        foreground_pid, _ = self.window_identity(foreground)
        shell_pid, _ = self.window_identity(shell)
        session = self.session(os.getpid())
        return {
            "elevated": bool(elevation.value),
            "ssh": any(os.environ.get(k) for k in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")),
            "remote_session": bool(self.user.GetSystemMetrics(0x1000)),
            "remote_control": bool(self.user.GetSystemMetrics(0x2001)),
            "station": self.object_name(station), "station_visible": bool(flags[2] & 1),
            "thread_desktop": thread_name, "input_desktop": input_name,
            "session": session, "console_session": int(self.kernel.WTSGetActiveConsoleSessionId()),
            "foreground_visible": bool(self.user.IsWindowVisible(foreground)),
            "same_session": self.session(foreground_pid) == self.session(shell_pid) == session,
        }

    def require_desktop(self) -> dict:
        contained = wt.BOOL()
        self.check(self.kernel.IsProcessInJob(
            self.kernel.GetCurrentProcess(), None, ct.byref(contained),
        ), "verify inherited test containment")
        if not contained.value:
            raise ObservationBlocked("desktop qualification requires its admitted outer Job owner")
        facts = self.facts()
        facts["inherited_job_membership"] = True
        if (
            facts["elevated"] or facts["ssh"] or facts["remote_session"] or facts["remote_control"]
            or facts["station"] != "WinSta0" or not facts["station_visible"]
            or facts["thread_desktop"] != "Default" or facts["input_desktop"] != "Default"
            or not facts["session"] or facts["session"] != facts["console_session"]
            or not facts["foreground_visible"] or not facts["same_session"]
        ):
            raise ObservationBlocked(f"normal local console is not admitted: {facts}")
        return facts

    def foreground(self) -> int:
        return int(self.user.GetForegroundWindow() or 0)

    def window_identity(self, hwnd: int) -> tuple[int, int]:
        pid = wt.DWORD()
        thread = self.user.GetWindowThreadProcessId(hwnd, ct.byref(pid))
        self.check(thread and pid.value, "window PID/TID")
        return int(pid.value), int(thread)

    def raw_windows(self) -> dict[int, tuple[int, int]]:
        result, errors = {}, []

        @WindowCallback
        def visit(hwnd, _unused):
            if self.user.IsWindowVisible(hwnd):
                try:
                    result[int(hwnd)] = self.window_identity(hwnd)
                except Exception as error:
                    errors.append(error)
                    return False
            return True

        complete = self.user.EnumWindows(visit, 0)
        if errors:
            raise errors[0]
        self.check(complete, "enumerate visible top-level windows")
        return result

    def event_hooks(self, receive, on_error):
        return EventHooks(self, receive, on_error)

    def pump(self) -> None:
        message = wt.MSG()
        for _ in range(4096):
            if not self.user.PeekMessageW(ct.byref(message), None, 0, 0, 1):
                return
            if message.message == 0x12:
                raise ObservationBlocked("unexpected WM_QUIT in desktop message pump")
            self.user.TranslateMessage(ct.byref(message))
            self.user.DispatchMessageW(ct.byref(message))
        raise ObservationBlocked("desktop message queue exceeded its bounded drain")
