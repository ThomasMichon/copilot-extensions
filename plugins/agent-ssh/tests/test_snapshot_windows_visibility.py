"""Opt-in observation of the completed-runtime native chain from a GUI parent."""

from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import subprocess
import sys
import time

import pytest

pytestmark = pytest.mark.skipif(
    sys.platform != "win32" or os.environ.get("AGENT_SSH_WINDOWS_VISIBILITY") != "1",
    reason="requires explicit native Windows visibility acceptance",
)
_PLUGIN = Path(__file__).resolve().parents[1]


def _load(path, name):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec and spec.loader
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module


def _require_native_payload(payload):
    assert (payload / "scripts" / "snapshot_runtime.py").is_file(), "native adapter missing"
    assert (payload / "scripts" / "snapshot_worktrees.py").is_file(), (
        "optional owner adapter missing"
    )


def test_old_shell_only_fixture_is_not_native_acceptance(tmp_path):
    (tmp_path / "scripts").mkdir()
    (tmp_path / "scripts" / "emit-mesh-pointer.ps1").write_text("exit 0\n")
    with pytest.raises(AssertionError, match="native adapter missing"):
        _require_native_payload(tmp_path)


def _interactive_desktop(user32, kernel32):
    import ctypes
    from ctypes import wintypes

    class Flags(ctypes.Structure):
        _fields_ = [
            ("inherit", wintypes.BOOL),
            ("reserved", wintypes.BOOL),
            ("flags", wintypes.DWORD),
        ]

    user32.GetProcessWindowStation.restype = wintypes.HANDLE
    user32.GetThreadDesktop.argtypes = [wintypes.DWORD]
    user32.GetThreadDesktop.restype = wintypes.HANDLE
    user32.OpenInputDesktop.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    user32.OpenInputDesktop.restype = wintypes.HANDLE
    user32.CloseDesktop.argtypes = [wintypes.HANDLE]
    user32.GetUserObjectInformationW.argtypes = [
        wintypes.HANDLE,
        ctypes.c_int,
        ctypes.c_void_p,
        wintypes.DWORD,
        ctypes.POINTER(wintypes.DWORD),
    ]
    needed = wintypes.DWORD()
    flags = Flags()
    if (
        not user32.GetUserObjectInformationW(
            user32.GetProcessWindowStation(),
            1,
            ctypes.byref(flags),
            ctypes.sizeof(flags),
            ctypes.byref(needed),
        )
        or not flags.flags & 1
    ):
        pytest.fail("BLOCKED: native observer has no visible interactive window station")
    desktop = user32.OpenInputDesktop(0, False, 1)
    if not desktop:
        pytest.fail(
            f"BLOCKED: native observer cannot open the input desktop (Win32 {ctypes.get_last_error()})"
        )
    try:

        def name(handle):
            value = ctypes.create_unicode_buffer(256)
            if not user32.GetUserObjectInformationW(
                handle,
                2,
                value,
                ctypes.sizeof(value),
                ctypes.byref(needed),
            ):
                pytest.fail("BLOCKED: native observer cannot identify the desktop")
            return value.value

        current = user32.GetThreadDesktop(kernel32.GetCurrentThreadId())
        if name(current) != name(desktop) or not user32.GetForegroundWindow():
            pytest.fail("BLOCKED: native observer is not on the active foreground desktop")
        return name(desktop)
    finally:
        user32.CloseDesktop(desktop)


@pytest.mark.skipif(
    os.environ.get("_SSH_VISIBILITY_CHILD") != "1", reason="GUI parent scenario only"
)
def test_windowless_native_child_scenario(tmp_path):
    import ctypes
    from ctypes import wintypes

    ctypes.windll.kernel32.GetConsoleWindow.restype = wintypes.HWND
    assert not ctypes.windll.kernel32.GetConsoleWindow(), "parent unexpectedly has a console"
    native = _load(_PLUGIN / "tests" / "test_snapshot_runtime.py", "ssh_native_visibility_driver")
    fixture = json.loads(os.environ["_SSH_VISIBILITY_FIXTURE"])
    payload, caller, output = (Path(fixture[key]) for key in ("payload", "caller", "output"))
    output.mkdir()
    _require_native_payload(payload)
    # A regression to the shell route cannot produce a false positive.
    (payload / "scripts" / "emit-mesh-pointer.ps1").write_text(
        "throw 'native visibility acceptance forbids shell fallback'\n",
        encoding="utf-8",
    )
    samples, captures = [], []
    for _ in range(2):
        started = time.monotonic()
        assert native.writer.write_session_guidance(
            {"sessionId": "session-1", "cwd": str(caller)},
            home=output,
        )
        samples.append(round(time.monotonic() - started, 3))
        value = native._snapshot(output)
        assert value["topology"]["status"] == "available"
        assert value["topology"]["origin"] == "external-state"
        assert value["state_root"] == "bound"
        assert value["connectivity"] == value["effective_routes"] == "unobserved"
        captures.append(value["captured_at"])
    assert captures[0] != captures[1]
    adapter = payload / "scripts" / "snapshot_runtime.py"
    backup = adapter.with_suffix(".disabled")
    adapter.rename(backup)
    try:
        assert native.writer.write_session_guidance(
            {"sessionId": "session-1", "cwd": str(caller)},
            home=output,
        )
        with pytest.raises(StopIteration):
            native._snapshot(output)
    finally:
        backup.rename(adapter)
    cleanup = native._optional_deadline_controls(
        tmp_path / "cleanup",
        {"agent-ssh": payload},
        caller,
    )
    Path(fixture["report"]).write_text(
        json.dumps(
            {
                "parent_console": False,
                "callbacks": 2,
                "samples_seconds": samples,
                "same_session_refreshed": True,
                "native_adapter": True,
                "optional_owner": "available",
                "missing_adapter_red_control": True,
                "cleanup": cleanup,
            }
        ),
        encoding="utf-8",
    )


@pytest.mark.timeout(150)
def test_windowless_writer_chain_two_cycles_and_timeout(tmp_path, monkeypatch, capsys):
    import ctypes
    from ctypes import wintypes
    import agent_procutil

    helpers = _load(
        _PLUGIN.parents[1]
        / "libs"
        / "agent-procutil"
        / "tests"
        / "test_spawn_daemon_windows_integration.py",
        "ssh_window_observer",
    )
    helpers._SURFACED_PROCESS_NAMES.add("pwsh.exe")
    user32, kernel32, callback_type = helpers._configure_win32()
    desktop = _interactive_desktop(user32, kernel32)
    native = _load(_PLUGIN / "tests" / "test_snapshot_runtime.py", "ssh_visibility_fixture")
    configured = os.environ.get("AGENT_SSH_NATIVE_CALLER")
    _, payloads = native._cell(
        tmp_path / "cell", monkeypatch, host_context=bool(configured), legacy=True
    )
    caller = (
        Path(configured).resolve(strict=True)
        if configured
        else native._configured_caller(tmp_path)[0]
    )
    _require_native_payload(payloads["agent-ssh"])
    report = tmp_path / "report.json"
    environment = {
        **os.environ,
        **agent_procutil.windowless_python_env(sys.executable),
        "_SSH_VISIBILITY_CHILD": "1",
        "_SSH_VISIBILITY_FIXTURE": json.dumps(
            {
                "payload": str(payloads["agent-ssh"]),
                "caller": str(caller),
                "output": str(tmp_path / "output"),
                "report": str(report),
            }
        ),
    }
    pythonw = agent_procutil.windowless_python(sys.executable, require_windowless=True)

    class Observer(helpers._WindowObserver):
        def _run(self):
            previous = self.foreground
            while not self._stop.is_set():
                for hwnd in helpers._visible_windows(user32, callback_type) - self.baseline:
                    if name := self._attributable(hwnd):
                        self.surfaced[hwnd] = name
                current = user32.GetForegroundWindow()
                # Include transitions to existing console windows, not just
                # newly-created windows absent from the baseline.
                if current and current != previous and (name := self._attributable(current)):
                    self.focus_changes[current] = name
                previous = current
                time.sleep(0.01)

    with Observer(user32, kernel32, callback_type) as observer:
        process, job = agent_procutil.spawn_sync_in_kill_on_close_job(
            [
                pythonw,
                "-m",
                "pytest",
                str(Path(__file__)),
                "-q",
                "-k",
                "windowless_native_child_scenario",
                "--basetemp",
                str(tmp_path / "child-tests"),
            ],
            require_job=True,
            allow_breakaway=False,
            env=environment,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            text=True,
            encoding="utf-8",
            **agent_procutil.no_window_kwargs(),
        )
        try:
            handle = kernel32.OpenProcess(0x1000, False, process.pid)
            assert handle
            try:
                length = wintypes.DWORD(32768)
                image = ctypes.create_unicode_buffer(length.value)
                assert kernel32.QueryFullProcessImageNameW(handle, 0, image, ctypes.byref(length))
                assert Path(image.value).name.lower() == "pythonw.exe", (
                    "parent is not native GUI Python"
                )
            finally:
                kernel32.CloseHandle(handle)
            stdout, stderr = process.communicate(timeout=90)
            assert process.returncode == 0, stdout + stderr
            assert "1 passed" in stdout, stdout
        finally:
            if job is not None and job.handle:
                try:
                    job.terminate_and_wait(2.0)
                finally:
                    job.close()
            process.communicate(timeout=2.0)
            for stream in (process.stdout, process.stderr):
                if stream is not None:
                    stream.close()
        time.sleep(0.1)
    assert observer.surfaced == {}, f"visible native-chain windows: {observer.surfaced}"
    assert observer.focus_changes == {}, (
        f"native-chain foreground transitions: {observer.focus_changes}"
    )
    value = json.loads(report.read_text(encoding="utf-8"))
    assert value["callbacks"] == 2 and value["native_adapter"]
    assert {item["phase"] for item in value["cleanup"]} == {"resolution", "execution"}
    assert all(item["descendant_reaped"] for item in value["cleanup"])
    with capsys.disabled():
        print(
            json.dumps(
                {
                    "measurement": "native-windows-windowless-parent-observer",
                    "desktop": desktop,
                    "visible_windows": 0,
                    "foreground_transitions": 0,
                    **value,
                    "cache": "not-controlled",
                    "composed_two_second_gate": "unqualified",
                }
            )
        )
