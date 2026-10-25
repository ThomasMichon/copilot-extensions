"""Read-only Win32 identity/desktop observations; imports are portable."""

from __future__ import annotations

import ctypes as ct
from ctypes import wintypes as wt
from dataclasses import asdict, dataclass
import os
from pathlib import Path, PureWindowsPath
import threading
import time

from native import UnsupportedNative

CONSOLE_IMAGES = {
    "python.exe", "pythonw.exe", "uv.exe", "cmd.exe", "powershell.exe", "pwsh.exe",
    "conhost.exe", "openconsole.exe", "windowsterminal.exe",
}


class NativeBlocked(UnsupportedNative):
    def __init__(self, code: str, message: str) -> None:
        self.code = code
        super().__init__(f"{code}: {message}")


@dataclass(frozen=True)
class DesktopFacts:
    elevated: bool
    shell_present: bool
    foreground_visible: bool
    station_visible: bool
    input_desktop_matches: bool
    same_session: bool
    ssh: bool


def require_desktop(facts: DesktopFacts) -> dict[str, bool]:
    checks = (
        (facts.elevated, "elevated-token", "use a normal unelevated Windows console"),
        (facts.ssh, "ssh-session", "SSH is not an interactive desktop qualification venue"),
        (not facts.shell_present, "shell-unavailable", "GetShellWindow is unavailable"),
        (not facts.foreground_visible, "foreground-unavailable", "visible foreground access is required"),
        (not facts.station_visible, "station-not-interactive", "a visible WinSta0 station is required"),
        (not facts.input_desktop_matches, "desktop-not-interactive", "current input desktop is inaccessible"),
        (not facts.same_session, "desktop-session-mismatch", "shell and foreground must share this session"),
    )
    for failed, code, message in checks:
        if failed:
            raise NativeBlocked(code, message)
    return asdict(facts)


def create_private_directory(path: Path) -> None:
    """Create only the new fixture root, with a protected inheritable user DACL."""
    if os.name != "nt":
        raise NativeBlocked("win32-unavailable", "Windows private directory API unavailable")
    kernel = ct.WinDLL("kernel32", use_last_error=True)
    advapi = ct.WinDLL("advapi32", use_last_error=True)
    kernel.GetCurrentProcess.restype = wt.HANDLE
    kernel.GetVolumePathNameW.argtypes = [wt.LPCWSTR, wt.LPWSTR, wt.DWORD]
    kernel.GetVolumeInformationW.argtypes = [
        wt.LPCWSTR, wt.LPWSTR, wt.DWORD, ct.POINTER(wt.DWORD), ct.POINTER(wt.DWORD),
        ct.POINTER(wt.DWORD), wt.LPWSTR, wt.DWORD,
    ]
    volume, flags = ct.create_unicode_buffer(32768), wt.DWORD()
    if not kernel.GetVolumePathNameW(str(path.parent), volume, len(volume)):
        raise ct.WinError(ct.get_last_error())
    if not kernel.GetVolumeInformationW(volume, None, 0, None, None, ct.byref(flags), None, 0):
        raise ct.WinError(ct.get_last_error())
    if not flags.value & 0x8:
        raise NativeBlocked("private-acl-unsupported", "fixture volume does not preserve Windows ACLs")
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.LocalFree.argtypes, kernel.LocalFree.restype = [wt.LPVOID], wt.LPVOID
    advapi.OpenProcessToken.argtypes = [wt.HANDLE, wt.DWORD, ct.POINTER(wt.HANDLE)]
    advapi.GetTokenInformation.argtypes = [
        wt.HANDLE, ct.c_int, wt.LPVOID, wt.DWORD, ct.POINTER(wt.DWORD),
    ]
    advapi.ConvertSidToStringSidW.argtypes = [wt.LPVOID, ct.POINTER(wt.LPWSTR)]
    advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW.argtypes = [
        wt.LPCWSTR, wt.DWORD, ct.POINTER(wt.LPVOID), ct.POINTER(wt.DWORD),
    ]

    class Attributes(ct.Structure):
        _fields_ = [("length", wt.DWORD), ("descriptor", wt.LPVOID), ("inherit", wt.BOOL)]

    kernel.CreateDirectoryW.argtypes = [wt.LPCWSTR, ct.POINTER(Attributes)]
    token, sid_text, descriptor = wt.HANDLE(), wt.LPWSTR(), wt.LPVOID()
    if not advapi.OpenProcessToken(kernel.GetCurrentProcess(), 0x8, ct.byref(token)):
        raise ct.WinError(ct.get_last_error())
    try:
        required = wt.DWORD()
        advapi.GetTokenInformation(token, 1, None, 0, ct.byref(required))
        if not required.value:
            raise ct.WinError(ct.get_last_error())
        user = ct.create_string_buffer(required.value)
        if not advapi.GetTokenInformation(token, 1, user, len(user), ct.byref(required)):
            raise ct.WinError(ct.get_last_error())
        sid = ct.cast(user, ct.POINTER(wt.LPVOID)).contents
        if not advapi.ConvertSidToStringSidW(sid, ct.byref(sid_text)):
            raise ct.WinError(ct.get_last_error())
        sddl = f"D:P(A;OICI;FA;;;{sid_text.value})(A;OICI;FA;;;BA)"
        if not advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW(
            sddl, 1, ct.byref(descriptor), None,
        ):
            raise ct.WinError(ct.get_last_error())
        attributes = Attributes(ct.sizeof(Attributes), descriptor, False)
        if not kernel.CreateDirectoryW(str(path), ct.byref(attributes)):
            raise ct.WinError(ct.get_last_error())
    finally:
        if descriptor:
            kernel.LocalFree(descriptor)
        if sid_text:
            kernel.LocalFree(ct.cast(sid_text, wt.LPVOID))
        kernel.CloseHandle(token)


@dataclass(frozen=True)
class ProcessInfo:
    pid: int
    created: int
    parent: int
    image: str
    session: int

    @property
    def key(self) -> tuple[int, int]:
        return self.pid, self.created


@dataclass(frozen=True)
class WindowInfo:
    hwnd: int
    process: ProcessInfo
    lineage: tuple[tuple[int, int], ...]

    @property
    def key(self) -> tuple[int, int, int]:
        return self.hwnd, self.process.pid, self.process.created


class WinAPI:
    def __init__(self) -> None:
        if os.name != "nt":
            raise NativeBlocked("win32-unavailable", "native Windows APIs are unavailable")
        self.kernel = ct.WinDLL("kernel32", use_last_error=True)
        self.user = ct.WinDLL("user32", use_last_error=True)
        self.advapi = ct.WinDLL("advapi32", use_last_error=True)
        self.callback = ct.WINFUNCTYPE(wt.BOOL, wt.HWND, wt.LPARAM)
        self.handles: dict[int, int] = {}
        self.infos: dict[int, ProcessInfo] = {}
        self.parents: dict[int, int] = {}
        self.lock = threading.RLock()
        try:
            self._configure()
        except (AttributeError, OSError) as exc:
            raise NativeBlocked("required-win32-api-unavailable", str(exc)) from exc

    def _configure(self) -> None:
        k, u, a = self.kernel, self.user, self.advapi
        signatures = (
            (k.OpenProcess, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            (k.CloseHandle, [wt.HANDLE], wt.BOOL),
            (k.GetCurrentProcess, [], wt.HANDLE),
            (k.GetCurrentThreadId, [], wt.DWORD),
            (k.ProcessIdToSessionId, [wt.DWORD, ct.POINTER(wt.DWORD)], wt.BOOL),
            (k.GetProcessTimes, [wt.HANDLE, *([ct.POINTER(ct.c_ulonglong)] * 4)], wt.BOOL),
            (k.QueryFullProcessImageNameW,
             [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)], wt.BOOL),
            (a.OpenProcessToken, [wt.HANDLE, wt.DWORD, ct.POINTER(wt.HANDLE)], wt.BOOL),
            (a.GetTokenInformation,
             [wt.HANDLE, ct.c_int, wt.LPVOID, wt.DWORD, ct.POINTER(wt.DWORD)], wt.BOOL),
            (u.GetShellWindow, [], wt.HWND), (u.GetForegroundWindow, [], wt.HWND),
            (u.IsWindowVisible, [wt.HWND], wt.BOOL),
            (u.GetWindowThreadProcessId, [wt.HWND, ct.POINTER(wt.DWORD)], wt.DWORD),
            (u.EnumWindows, [self.callback, wt.LPARAM], wt.BOOL),
            (u.GetProcessWindowStation, [], wt.HANDLE),
            (u.GetThreadDesktop, [wt.DWORD], wt.HANDLE),
            (u.OpenInputDesktop, [wt.DWORD, wt.BOOL, wt.DWORD], wt.HANDLE),
            (u.CloseDesktop, [wt.HANDLE], wt.BOOL),
            (u.GetUserObjectInformationW,
             [wt.HANDLE, ct.c_int, wt.LPVOID, wt.DWORD, ct.POINTER(wt.DWORD)], wt.BOOL),
        )
        for function, arguments, result in signatures:
            function.argtypes, function.restype = arguments, result

    def _check(self, value: object, action: str) -> None:
        if not value:
            raise NativeBlocked("win32-inspection-failed", f"{action}: WinError {ct.get_last_error()}")

    def elevated(self, process: int) -> bool:
        token, elevation, size = wt.HANDLE(), wt.DWORD(), wt.DWORD()
        self._check(self.advapi.OpenProcessToken(process, 0x8, ct.byref(token)), "OpenProcessToken")
        try:
            self._check(
                self.advapi.GetTokenInformation(
                    token, 20, ct.byref(elevation), ct.sizeof(elevation), ct.byref(size),
                ), "TokenElevation",
            )
            return bool(elevation.value)
        finally:
            self.kernel.CloseHandle(token)

    def session(self, pid: int) -> int:
        session = wt.DWORD()
        self._check(self.kernel.ProcessIdToSessionId(pid, ct.byref(session)), "ProcessIdToSessionId")
        return int(session.value)

    def _object_name(self, handle: int) -> str:
        buffer, size = ct.create_unicode_buffer(256), wt.DWORD()
        self._check(
            self.user.GetUserObjectInformationW(handle, 2, buffer, ct.sizeof(buffer), ct.byref(size)),
            "desktop/station name",
        )
        return buffer.value

    def facts(self) -> DesktopFacts:
        shell, foreground = self.user.GetShellWindow(), self.user.GetForegroundWindow()
        shell_pid, foreground_pid = wt.DWORD(), wt.DWORD()
        if shell:
            self._check(self.user.GetWindowThreadProcessId(shell, ct.byref(shell_pid)), "shell PID")
        if foreground:
            self._check(
                self.user.GetWindowThreadProcessId(foreground, ct.byref(foreground_pid)), "foreground PID",
            )
        station = self.user.GetProcessWindowStation()
        self._check(station, "GetProcessWindowStation")
        flags, size = (wt.DWORD * 3)(), wt.DWORD()
        self._check(
            self.user.GetUserObjectInformationW(
                station, 1, flags, ct.sizeof(flags), ct.byref(size),
            ), "window station visibility",
        )
        desktop = self.user.OpenInputDesktop(0, False, 0x0001 | 0x0100)
        matches = False
        if desktop:
            try:
                current = self.user.GetThreadDesktop(self.kernel.GetCurrentThreadId())
                matches = self._object_name(desktop) == self._object_name(current) == "Default"
            finally:
                self.user.CloseDesktop(desktop)
        current_session = self.session(os.getpid())
        return DesktopFacts(
            elevated=self.elevated(self.kernel.GetCurrentProcess()),
            shell_present=bool(shell),
            foreground_visible=bool(foreground and self.user.IsWindowVisible(foreground)),
            station_visible=self._object_name(station) == "WinSta0" and bool(flags[2] & 1),
            input_desktop_matches=matches,
            same_session=bool(
                current_session and shell_pid.value and foreground_pid.value
                and self.session(shell_pid.value) == self.session(foreground_pid.value) == current_session
            ),
            ssh=any(os.environ.get(key) for key in ("SSH_CONNECTION", "SSH_CLIENT", "SSH_TTY")),
        )

    def refresh_processes(self) -> None:
        class Entry(ct.Structure):
            _fields_ = [
                ("dwSize", wt.DWORD), ("cntUsage", wt.DWORD), ("pid", wt.DWORD),
                ("heap", ct.c_size_t), ("module", wt.DWORD), ("threads", wt.DWORD),
                ("parent", wt.DWORD), ("priority", wt.LONG), ("flags", wt.DWORD),
                ("exe", wt.WCHAR * 260),
            ]
        snapshot = self.kernel.CreateToolhelp32Snapshot
        snapshot.argtypes, snapshot.restype = [wt.DWORD, wt.DWORD], wt.HANDLE
        first, next_entry = self.kernel.Process32FirstW, self.kernel.Process32NextW
        for function in (first, next_entry):
            function.argtypes, function.restype = [wt.HANDLE, ct.POINTER(Entry)], wt.BOOL
        handle = snapshot(2, 0)
        if not handle or handle == ct.c_void_p(-1).value:
            raise NativeBlocked("process-snapshot-failed", "CreateToolhelp32Snapshot failed")
        try:
            entry = Entry()
            entry.dwSize = ct.sizeof(entry)
            parents = {}
            available = first(handle, ct.byref(entry))
            if not available and ct.get_last_error() != 18:
                raise NativeBlocked("process-snapshot-failed", "Process32FirstW failed")
            while available:
                parents[int(entry.pid)] = int(entry.parent)
                available = next_entry(handle, ct.byref(entry))
            if ct.get_last_error() != 18:
                raise NativeBlocked("process-snapshot-failed", "Process32NextW failed")
            self.parents = parents
        finally:
            self.kernel.CloseHandle(handle)

    def process(self, pid: int) -> ProcessInfo:
        with self.lock:
            return self._process(pid)

    def _process(self, pid: int) -> ProcessInfo:
        if pid in self.infos:
            return self.infos[pid]
        handle = self.kernel.OpenProcess(0x100000 | 0x1000, False, pid)
        self._check(handle, "pin process identity")
        try:
            times = [ct.c_ulonglong() for _ in range(4)]
            self._check(
                self.kernel.GetProcessTimes(handle, *(ct.byref(item) for item in times)),
                "process creation time",
            )
            image, size = ct.create_unicode_buffer(32768), wt.DWORD(32768)
            self._check(
                self.kernel.QueryFullProcessImageNameW(handle, 0, image, ct.byref(size)),
                "process executable identity",
            )
            if pid not in self.parents:
                self.refresh_processes()
            info = ProcessInfo(
                pid, times[0].value, self.parents.get(pid, 0),
                PureWindowsPath(image.value).name.lower(), self.session(pid),
            )
            self.handles[pid], self.infos[pid] = handle, info
            return info
        except BaseException:
            self.kernel.CloseHandle(handle)
            raise

    def lineage(self, pid: int) -> tuple[tuple[int, int], ...]:
        result = []
        child_created = 1 << 64
        for _ in range(128):
            if pid <= 0:
                break
            try:
                info = self.process(pid)
            except NativeBlocked:
                break
            if info.created > child_created or info.key in result:
                break
            result.append(info.key)
            child_created, pid = info.created, info.parent
        return tuple(result)

    def windows(self) -> dict[int, WindowInfo]:
        result = {}
        failure = []

        @self.callback
        def visit(hwnd: int, _data: int) -> bool:
            if self.user.IsWindowVisible(hwnd):
                try:
                    pid = wt.DWORD()
                    self._check(
                        self.user.GetWindowThreadProcessId(hwnd, ct.byref(pid)), "visible window PID",
                    )
                    result[int(hwnd)] = WindowInfo(int(hwnd), self.process(pid.value), self.lineage(pid.value))
                except Exception as exc:
                    # ctypes otherwise prints and discards Python callback exceptions.
                    failure.append(NativeBlocked(
                        "window-callback-failed", f"{type(exc).__name__}: {exc}",
                    ))
                    return False
            return True

        enumerated = self.user.EnumWindows(visit, 0)
        if failure:
            raise failure[0]
        self._check(enumerated, "EnumWindows")
        return result

    def foreground(self) -> int:
        foreground = self.user.GetForegroundWindow()
        self._check(foreground, "GetForegroundWindow during observation")
        return int(foreground)

    def close(self) -> None:
        for handle in self.handles.values():
            self.kernel.CloseHandle(handle)
        self.handles.clear()


class WindowObserver:
    def __init__(self, api: WinAPI) -> None:
        self.api = api
        api.refresh_processes()
        self.baseline = {window.key for window in api.windows().values()}
        self.foreground = api.foreground()
        self.roots = {api.process(os.getpid()).key}
        self.events: dict[tuple[str, int, int, int], WindowInfo] = {}
        self.samples = 0
        self.max_gap = 0.0
        self.started = self.previous = time.monotonic()
        self.error: str | None = None
        self.stop = threading.Event()
        self.thread = threading.Thread(target=self._run, daemon=True)

    def add_pid(self, pid: int) -> None:
        info = self.api.process(pid)
        if info.session != self.api.session(os.getpid()):
            raise NativeBlocked("fixture-session-mismatch", "fixture child left the interactive session")
        if self.api.elevated(self.api.handles[pid]):
            raise NativeBlocked("fixture-elevated", "fixture child unexpectedly has an elevated token")
        self.roots.add(info.key)

    def sample(self) -> None:
        now = time.monotonic()
        self.max_gap = max(self.max_gap, now - self.previous)
        self.previous = now
        self.api.refresh_processes()
        windows = self.api.windows()
        for window in windows.values():
            if window.key not in self.baseline:
                self.events.setdefault(("visible", *window.key), window)
        foreground = self.api.foreground()
        if foreground != self.foreground:
            window = windows.get(foreground)
            if window is None:
                raise NativeBlocked("foreground-unobservable", "foreground identity disappeared")
            self.events.setdefault(("foreground", *window.key), window)
            self.foreground = foreground
        self.samples += 1
        if len(self.events) > 4096:
            raise NativeBlocked("window-event-overflow", "observation exceeded its bounded event packet")

    def _run(self) -> None:
        try:
            while not self.stop.wait(0.02):
                if self.samples % 50 == 0:
                    require_desktop(self.api.facts())
                self.sample()
        except Exception as exc:
            # A dead observer must never look like a successful zero-window trace.
            self.error = f"{type(exc).__name__}: {exc}"

    def __enter__(self) -> WindowObserver:
        self.thread.start()
        return self

    def __exit__(self, *_args: object) -> None:
        self.stop.set()
        self.thread.join(timeout=5)
        if self.thread.is_alive():
            self.error = "window observer did not quiesce"

    def packet(self) -> dict:
        violations = []
        for (kind, *_key), window in self.events.items():
            attributable = bool(set(window.lineage) & self.roots)
            ambiguous_console = window.process.image in CONSOLE_IMAGES
            if attributable or ambiguous_console or kind in {"visible", "foreground"}:
                violations.append({
                    "kind": kind, "pid": window.process.pid, "image": window.process.image,
                    "attribution": (
                        "fixture" if attributable else
                        "ambiguous-console-host" if ambiguous_console else f"ambiguous-desktop-{kind}"
                    ),
                })
        return {
            "baseline_visible_windows": len(self.baseline), "samples": self.samples,
            "observed_seconds": time.monotonic() - self.started,
            "poll_interval_seconds": 0.02, "maximum_observation_gap_seconds": self.max_gap,
            "observation_policy": "no new visible windows or foreground transitions",
            "violations": violations, "observer_error": self.error,
            "passed": not violations and self.error is None and self.max_gap <= 0.5 and self.samples > 0,
        }
