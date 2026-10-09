from __future__ import annotations

import importlib.util
import os
import subprocess
import sys
import time
from pathlib import Path

import pytest

_SCRIPT = Path(__file__).resolve().parents[1] / "scripts" / "terminal_probe.py"
_SPEC = importlib.util.spec_from_file_location("terminal_probe_under_test", _SCRIPT)
assert _SPEC and _SPEC.loader
terminal_probe = importlib.util.module_from_spec(_SPEC)
sys.modules[_SPEC.name] = terminal_probe
_SPEC.loader.exec_module(terminal_probe)


def test_probe_terminal_never_raises(monkeypatch):
    def boom():
        raise OSError("no win32")

    monkeypatch.setattr(terminal_probe.sys, "platform", "win32")
    monkeypatch.setattr(terminal_probe, "_probe_windows", boom)
    assert terminal_probe.probe() == {}


def test_probe_scopes_ancestry_to_linux_and_windows(monkeypatch):
    monkeypatch.setattr(terminal_probe, "_posix_ancestors", lambda: [7, 1])
    monkeypatch.setattr(terminal_probe.sys, "platform", "linux")
    assert terminal_probe.probe() == {"ancestors": [7, 1]}
    monkeypatch.setattr(terminal_probe.sys, "platform", "darwin")
    assert terminal_probe.probe() == {}


class _FakeKernel32:
    def __init__(self, consoles):
        self.consoles = consoles
        self.attached = None
        self.events = []
        self.std = {}

    def GetStdHandle(self, std_id):
        return f"orig-{std_id}"

    def SetStdHandle(self, std_id, handle):
        self.std[std_id] = handle

    def SetConsoleCtrlHandler(self, handler, add):
        self.events.append(("ctrl", add))

    def AttachConsole(self, pid):
        if pid not in self.consoles:
            return 0
        self.attached = pid
        self.events.append(("attach", pid))
        return 1

    def GetConsoleWindow(self):
        return self.consoles.get(self.attached)

    def FreeConsole(self):
        self.events.append(("free", self.attached))
        self.attached = None
        return 1


def test_attached_console_window_detaches_and_restores_std_handles():
    kernel32 = _FakeKernel32({20: None, 30: 4242})
    hwnd = terminal_probe._attached_console_window(
        kernel32, [10, 20, 30, 40], deadline=time.monotonic() + 5,
    )
    assert hwnd == 4242
    assert kernel32.attached is None
    assert [e for e in kernel32.events if e[0] != "ctrl"] == [
        ("attach", 20), ("free", 20), ("attach", 30), ("free", 30),
    ]
    assert kernel32.events[0] == ("ctrl", True)
    assert kernel32.events[-1] == ("ctrl", False)
    # Ctrl+C ignore is re-armed after each successful attach, before any use.
    for index, event in enumerate(kernel32.events):
        if event[0] == "attach":
            assert kernel32.events[index + 1] == ("ctrl", True)
    assert len(kernel32.std) == 3
    assert all(handle == f"orig-{std_id}" for std_id, handle in kernel32.std.items())


def test_attached_console_window_respects_deadline():
    kernel32 = _FakeKernel32({30: 4242})
    assert terminal_probe._attached_console_window(
        kernel32, [30], deadline=time.monotonic() - 1,
    ) is None
    assert ("attach", 30) not in kernel32.events


@pytest.mark.skipif(sys.platform != "win32", reason="Win32 console probe")
def test_windows_terminal_probe_runs_against_real_process_table():
    started = time.monotonic()
    probe = terminal_probe._probe_windows()
    assert time.monotonic() - started < 2.0
    assert os.getppid() in probe["ancestors"]
    for key in ("console_hwnd", "host_hwnd", "host_pid"):
        assert probe.get(key) is None or isinstance(probe[key], int)


def test_detached_console_window_parses_helper_and_fails_soft(monkeypatch):
    calls = []

    def fake_run(argv, **kwargs):
        calls.append((argv, kwargs))
        return subprocess.CompletedProcess(argv, 0, stdout=b"67922\r\n")

    monkeypatch.setattr(terminal_probe.subprocess, "run", fake_run)
    deadline = time.monotonic() + 5
    assert terminal_probe._detached_console_window([30, 40], deadline=deadline) == 67922
    argv, kwargs = calls[0]
    assert argv[-2:] == ["30", "40"]
    assert kwargs["creationflags"] == 0x00000008
    assert 0 < kwargs["timeout"] <= 5

    def slow_run(argv, **kwargs):
        raise subprocess.TimeoutExpired(argv, kwargs["timeout"])

    monkeypatch.setattr(terminal_probe.subprocess, "run", slow_run)
    assert terminal_probe._detached_console_window([30], deadline=deadline) is None
    assert terminal_probe._detached_console_window([30], deadline=time.monotonic() - 1) is None
    assert terminal_probe._detached_console_window([], deadline=deadline) is None

def test_host_record_classifies_conpty_and_classic_console_hosts():
    table = {77: (1, "WindowsTerminal.exe"), 88: (1, "conhost.exe")}
    conpty = terminal_probe._host_record(
        1001, (88, "PseudoConsoleWindow"), 2002, (77, "CASCADIA_HOSTING_WINDOW_CLASS"), table,
    )
    assert conpty == {
        "console_hwnd": 1001, "console_class": "PseudoConsoleWindow",
        "host_hwnd": 2002, "host_pid": 77,
        "host_class": "CASCADIA_HOSTING_WINDOW_CLASS", "host_exe": "WindowsTerminal.exe",
    }
    classic = terminal_probe._host_record(
        3003, (88, "ConsoleWindowClass"), 3003, (None, None), table,
    )
    assert classic["host_hwnd"] == 3003
    assert classic["host_pid"] == 88
    assert classic["host_exe"] == "conhost.exe"
    unknown = terminal_probe._host_record(4004, (99, "Other"), None, (None, None), {})
    assert "host_hwnd" not in unknown and unknown["console_hwnd"] == 4004
