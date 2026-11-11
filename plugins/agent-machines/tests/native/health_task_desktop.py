"""Opportunistic desktop diagnostics for an already active console or RDP session."""

from __future__ import annotations


def require_observable_session(facts, *, station_name, connection_state, inherited_job):
    eligible_name = isinstance(station_name, str) and (
        station_name.casefold() == "console" or station_name.casefold().startswith("rdp-")
    )
    required = {
        "elevated": False, "station_visible": True,
        "foreground_visible": True, "same_session": True,
    }
    if (
        not eligible_name or inherited_job is not True
        or type(connection_state) is not int or connection_state != 0
        or type(facts.get("session")) is not int or facts["session"] <= 0
        or facts.get("station") != "WinSta0"
        or facts.get("thread_desktop") != "Default"
        or facts.get("input_desktop") != "Default"
        or any(facts.get(key) is not value for key, value in required.items())
    ):
        raise ValueError("optional-desktop-unavailable")
    return facts


def make_api():
    import ctypes as ct
    from ctypes import wintypes as wt
    import os

    from sdk_desktop_observer import ObservationBlocked
    from sdk_desktop_win32 import WinAPI

    class TaskDesktopAPI(WinAPI):
        def __init__(self):
            super().__init__()
            self.wts = ct.WinDLL("wtsapi32", use_last_error=True)
            self._bind(self.wts, {
                "WTSQuerySessionInformationW": (
                    [wt.HANDLE, wt.DWORD, ct.c_int, ct.POINTER(ct.c_void_p), ct.POINTER(wt.DWORD)],
                    wt.BOOL,
                ),
                "WTSFreeMemory": ([ct.c_void_p], None),
            })

        def session_info(self, session, kind):
            buffer, size = ct.c_void_p(), wt.DWORD()
            try:
                self.check(self.wts.WTSQuerySessionInformationW(
                    None, session, kind, ct.byref(buffer), ct.byref(size),
                ), "read own WTS session")
                if not buffer.value:
                    raise ObservationBlocked("own WTS data unavailable")
                if kind == 6 and 2 <= size.value <= 1024:
                    return ct.wstring_at(buffer, size.value // ct.sizeof(ct.c_wchar)).rstrip("\0")
                if kind == 8 and size.value == ct.sizeof(ct.c_int):
                    return ct.cast(buffer, ct.POINTER(ct.c_int)).contents.value
                raise ObservationBlocked("own WTS data malformed")
            finally:
                if buffer.value:
                    self.wts.WTSFreeMemory(buffer)

        def require_desktop(self):
            contained = wt.BOOL()
            self.check(self.kernel.IsProcessInJob(
                self.kernel.GetCurrentProcess(), None, ct.byref(contained),
            ), "verify inherited test containment")
            session = self.session(os.getpid())
            state = self.session_info(session, 8)
            if state != 0:
                raise ObservationBlocked(f"own WTS session inactive: state={state}")
            name = self.session_info(session, 6)
            facts = self.facts()
            try:
                require_observable_session(
                    facts, station_name=name, connection_state=state,
                    inherited_job=bool(contained.value),
                )
            except ValueError as error:
                raise ObservationBlocked(str(error)) from error
            return {**facts, "session_kind": "console" if name.casefold() == "console" else "rdp"}

    return TaskDesktopAPI()
