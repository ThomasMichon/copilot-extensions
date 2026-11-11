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


def task_owned_visual_violations(packet, identities, visible_confirmations):
    confirmed = list(visible_confirmations)
    for event in packet.get("violations", []):
        kind = event.get("kind")
        if kind not in (
            "sampled-foreground", "foreground-capture-changed",
            "foreground-enumeration-identity-mismatch",
        ):
            continue
        endpoints = [event.get("identity")]
        if kind == "foreground-capture-changed":
            endpoints.append(event.get("previous_identity"))
        if any(
            isinstance(identity, (list, tuple)) and len(identity) == 3
            and (identity[0], identity[2]) in identities for identity in endpoints
        ):
            confirmed.append(event)
    return confirmed


def confirm_visible_task_window(api, windows, identities, *, unavailable_error=RuntimeError):
    pids = {pid for pid, _birth in identities}
    for hwnd, owner in windows.items():
        if owner[0] not in pids:
            continue
        try:
            pin = api.pin(owner[0])
        except unavailable_error:
            continue
        try:
            if (
                (pin.pid, pin.birth) in identities and not pin.gone()
                and api.user.IsWindowVisible(hwnd) and api.window_identity(hwnd) == owner
                and not pin.gone()
            ):
                return {"kind": "task-visible-window", "pid": pin.pid, "birth": pin.birth}
        except unavailable_error:
            pass
        finally:
            try:
                pin.close()
            except unavailable_error:
                api.attribution_cleanup_error = True
                raise
    return None


def make_api():
    import ctypes as ct
    from ctypes import wintypes as wt
    import os
    import json
    from pathlib import Path
    import time

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
            self.receipt_paths = ()
            self.task_processes = set()
            self.confirmed_task_windows = []
            self.attribution_cleanup_error = False
            self.receipt_check = 0.0

        def prepare_task_observation(self, root):
            self.receipt_paths = tuple(Path(root) / name for name in ("console.jsonl", "resolver.jsonl"))

        def refresh_receipts(self, *, force=False):
            now = time.monotonic()
            if not force and now - self.receipt_check < .1:
                return
            self.receipt_check = now
            for path in self.receipt_paths:
                try:
                    if path.stat().st_size > 16384:
                        continue
                    content = path.read_text(encoding="utf-8-sig")
                except OSError:
                    continue
                for line in content.splitlines():
                    try:
                        row = json.loads(line)
                    except ValueError:
                        continue
                    if (
                        isinstance(row, dict) and type(row.get("pid")) is int and row["pid"] > 0
                        and type(row.get("birth")) is int and row["birth"] > 0
                    ):
                        self.task_processes.add((row["pid"], row["birth"]))

        def owned_identities(self):
            self.refresh_receipts(force=True)
            return frozenset(self.task_processes)

        def raw_windows(self):
            rows = super().raw_windows()
            self.refresh_receipts()
            if self.confirmed_task_windows:
                return rows
            confirmed = confirm_visible_task_window(
                self, rows, self.task_processes, unavailable_error=ObservationBlocked,
            )
            if confirmed:
                self.confirmed_task_windows.append(confirmed)
            return rows

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
