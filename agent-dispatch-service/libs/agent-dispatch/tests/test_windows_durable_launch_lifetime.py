"""Real Windows launch seams survive successful caller exit AND Job close."""

from __future__ import annotations

import ctypes
from ctypes import wintypes
import os
from pathlib import Path
import subprocess
import sys
import time

import agent_procutil as pu
import pytest

from . import windows_durable_launch_fixture as fixture

pytestmark = [
    pytest.mark.timeout(120),
    pytest.mark.skipif(os.name != "nt", reason="native Windows Job lifetime"),
    pytest.mark.portfolio_tier("T2"),
    pytest.mark.effect("filesystem"),
    pytest.mark.effect("process"),
    pytest.mark.contract("agent_dispatch.windows_durable_launch_lifetime"),
]


class DesktopProbe:
    """Same EnumWindows/foreground approach as test_procutil's live probe."""

    def __init__(self):
        self.api = ctypes.WinDLL("user32", use_last_error=True)
        self.callback = ctypes.WINFUNCTYPE(
            wintypes.BOOL, wintypes.HWND, wintypes.LPARAM,
        )
        self.api.GetForegroundWindow.restype = wintypes.HWND
        self.api.IsWindowVisible.argtypes = [wintypes.HWND]
        self.api.GetWindowThreadProcessId.argtypes = [
            wintypes.HWND, ctypes.POINTER(wintypes.DWORD),
        ]
        self.api.EnumWindows.argtypes = [self.callback, wintypes.LPARAM]
        self.api.GetProcessWindowStation.restype = wintypes.HANDLE
        self.api.GetUserObjectInformationW.argtypes = [
            wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD,
            ctypes.POINTER(wintypes.DWORD),
        ]

        class Flags(ctypes.Structure):
            _fields_ = [
                ("inherit", wintypes.BOOL), ("reserved", wintypes.BOOL),
                ("flags", wintypes.DWORD),
            ]

        flags = Flags()
        length = wintypes.DWORD()
        assert self.api.GetUserObjectInformationW(
            self.api.GetProcessWindowStation(), 1, ctypes.byref(flags),
            ctypes.sizeof(flags), ctypes.byref(length),
        )
        self.available = bool(flags.flags & 1)
        self.baseline = self.windows() if self.available else {}
        self.surfaced = set()
        self.focus = set()
        self.owned = set()
        self.console_windows = set()

    def windows(self):
        result = {}

        @self.callback
        def visit(hwnd, _):
            if self.api.IsWindowVisible(hwnd):
                pid = wintypes.DWORD()
                self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
                result[int(hwnd)] = pid.value
            return True

        assert self.api.EnumWindows(visit, 0)
        return result

    def sample(self):
        if not self.available:
            return
        windows = self.windows()
        for hwnd, pid in windows.items():
            if (
                hwnd in self.console_windows
                or (hwnd not in self.baseline and pid in self.owned)
            ):
                self.surfaced.add(hwnd)
        hwnd = self.api.GetForegroundWindow()
        if hwnd and int(hwnd) not in self.baseline:
            pid = wintypes.DWORD()
            self.api.GetWindowThreadProcessId(hwnd, ctypes.byref(pid))
            if int(hwnd) in self.console_windows or pid.value in self.owned:
                self.focus.add(int(hwnd))


@pytest.mark.parametrize("site", ["coordinator", "logon-helper"])
@pytest.mark.parametrize("contained", [False, True], ids=["production", "contained"])
def test_windows_durable_launch_lifetime(tmp_path, site, contained, record_property):
    root = tmp_path / "runtime with spaces"
    root.mkdir()
    installer = Path(__file__).resolve().parents[1] / "scripts" / "install.ps1"
    if site == "logon-helper":
        fixture.powershell_scripts(root, installer)
    env = dict(os.environ)
    env["AGENT_DISPATCH_INSTALL_DIR"] = str(root)
    env["PYTHONPATH"] = os.pathsep.join(
        [str(Path(fixture.__file__).parent), *sys.path]
    )
    # This T2 fixture is the ONLY deliberate containment-policy isolation.
    # Parent pytest keeps every marker/root. No real service ever runs; escaped
    # processes are finite, birth-checked, pinned, and explicitly reaped below.
    if not contained:
        env.pop("COPILOT_EXTENSIONS_TEST_CONTAINED", None)
        env.pop("PYTEST_CURRENT_TEST", None)
    else:
        env["COPILOT_EXTENSIONS_TEST_CONTAINED"] = "1"

    api = fixture.kernel()
    probe = DesktopProbe()
    record_property("interactive_desktop_observation", probe.available)
    if not probe.available:
        print("Non-interactive window station: validating lifetime only; host lane owns desktop observation")
    handles = {}
    job = None
    caller = None
    cleanup = []
    recovery_errors = []

    def pin(name, record):
        if name in handles:
            return
        handle = api.OpenProcess(0x00100000 | 0x1000 | 0x0001, False, record["pid"])
        assert handle, f"cannot pin {name}"
        try:
            assert fixture.birth(api, handle) == record["birth"], f"reused PID: {name}"
        except BaseException:
            api.CloseHandle(handle)
            raise
        handles[name] = handle
        probe.owned.add(record["pid"])

    def sample():
        for path in root.glob("console-*.json"):
            state = fixture.read_json(path)
            if state["hwnd"]:
                probe.console_windows.add(state["hwnd"])
            if state["visible"]:
                probe.surfaced.add(state["hwnd"])
        for cycle in range(2):
            path = root / f"cycle-{cycle}.json"
            if path.exists():
                record = fixture.read_json(path)
                probe.owned.add(record["pid"])
        probe.sample()

    def wait(predicate, message, seconds=40):
        deadline = time.monotonic() + seconds
        while time.monotonic() < deadline:
            sample()
            failures = list(root.glob("failure-*.json"))
            if failures:
                pytest.fail("\n".join(path.read_text(encoding="utf-8") for path in failures))
            if predicate():
                return
            if caller is not None and caller.poll() not in (None, 0):
                pytest.fail(f"caller failed: {(root / 'caller.log').read_text()}")
            time.sleep(.02)
        log = root / "caller.log"
        pytest.fail(f"{message}; caller log: {log.read_text(errors='replace') if log.exists() else 'absent'}")

    try:
        with (root / "caller.log").open("wb") as log:
            caller, job = pu.spawn_sync_in_kill_on_close_job(
                [sys.executable, str(Path(fixture.__file__).resolve()),
                 "caller", str(root), site],
                env=env, cwd=root, stdin=subprocess.DEVNULL,
                stdout=log, stderr=log, **pu.no_window_kwargs(),
            )
            assert job is not None, "caller must enter its Job BEFORE executing"
            probe.owned.add(caller.pid)
            wait(lambda: (root / "ready.json").exists(), "fake service never became ready")
            records = fixture.read_json(root / "ready.json")
            records["ordinary"] = fixture.read_json(root / "ordinary.json")
            for name, record in records.items():
                pin(name, record)
                membership = wintypes.BOOL()
                assert api.IsProcessInJob(handles[name], job.handle, ctypes.byref(membership))
                assert bool(membership.value) == (contained or name == "ordinary")
            (root / "pinned").touch()
            wait(lambda: caller.poll() is not None, "caller did not exit successfully")
            assert caller.wait(timeout=2) == 0
            for handle in handles.values():
                assert api.WaitForSingleObject(handle, 0) == 0x102
            # Parent exit0 alone is insufficient: updater closes this Job in
            # finally even on success. This is the actual regression boundary.
            job.close()
            job = None
            assert api.WaitForSingleObject(handles["ordinary"], 3000) == 0
            for name, handle in handles.items():
                if name != "ordinary":
                    assert api.WaitForSingleObject(handle, 100 if not contained else 3000) == (
                        0 if contained else 0x102
                    ), f"{name}: incorrect lifetime after caller Job close"
            if not contained:
                (root / "cycles-start").touch()
                wait(lambda: (root / "cycles-done.json").exists(), "recurring children failed")
                assert fixture.read_json(root / "cycles-done.json") == {"cycles": 2}
                assert all((root / f"console-cycle-{i}.json").is_file() for i in range(2))
                assert api.WaitForSingleObject(handles["service"], 0) == 0x102
                assert probe.surfaced == probe.focus == set()
    finally:
        (root / "stop").touch()
        # Recover published ownership even when an earlier assertion fails
        # before the normal pin handshake. Never terminate by a bare PID.
        recovery = {}
        ready = root / "ready.json"
        if ready.exists():
            recovery.update(fixture.read_json(ready))
        spawned = root / "spawned.json"
        if spawned.exists() and "service" not in recovery:
            recovery["service"] = fixture.read_json(spawned)
        for name, record in recovery.items():
            try:
                pin(name, record)
            except AssertionError as error:
                recovery_errors.append(str(error))
        if caller is not None:
            if caller.poll() is None:
                caller.kill()
            caller.wait(timeout=3)
        if job is not None:
            job.close()
        for name, handle in handles.items():
            try:
                if api.WaitForSingleObject(handle, 3000) == 0x102:
                    assert api.TerminateProcess(handle, 1), f"cleanup failed: {name}"
                assert api.WaitForSingleObject(handle, 3000) == 0, f"not reaped: {name}"
                cleanup.append(name)
            finally:
                assert api.CloseHandle(handle)
        assert len(cleanup) == len(handles)
        assert not recovery_errors, recovery_errors
