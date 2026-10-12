"""Native census regressions; no shell, fresh identity, exact command matching."""

import os
import subprocess
import ctypes
from ctypes import wintypes

import pytest

from agent_worktrees import status_monitor_cutover, windows_process_census as census


def test_snapshot_rejects_unreadable_and_reused_processes(monkeypatch):
    monkeypatch.setattr(census.procs, "_win_kernel32", lambda: object())
    enumerations = []

    def enum(*, strict):
        assert strict
        enumerations.append(True)
        return [101, 202, 303, 404]

    monkeypatch.setattr(census.procs, "_win_enum_pids", enum)
    monkeypatch.setattr(census.procs, "_win_read_cwd", lambda k, pid: ("C:\\runtime", "python.exe"))
    times = iter(["a", "a", "b", "changed", "c", "c", None])
    monkeypatch.setattr(census.locks, "process_start_time", lambda pid: next(times))
    monkeypatch.setattr(census, "command_line", lambda pid: None if pid == 303 else "python -m agent_worktrees status-monitor")
    assert [row["pid"] for row in census.snapshot()] == [101]
    assert len(enumerations) == 1


def test_enumeration_failure_is_not_an_empty_census(monkeypatch):
    monkeypatch.setattr(census.procs, "_win_kernel32", lambda: object())

    def fail(**kwargs):
        raise OSError("native enumeration unavailable")

    monkeypatch.setattr(census.procs, "_win_enum_pids", fail)
    with pytest.raises(OSError):
        census.snapshot()


@pytest.mark.parametrize("bad_pointer", [False, True])
def test_command_line_unicode_bounds_and_handle_cleanup(monkeypatch, bad_pointer):
    class UnicodeString(ctypes.Structure):
        _fields_ = [("length", wintypes.USHORT), ("maximum_length", wintypes.USHORT),
                    ("buffer", ctypes.c_void_p)]

    encoded = '"C:\\Unicode\\λ\\python.exe" -m agent_worktrees status-monitor'.encode("utf-16-le")
    size = ctypes.sizeof(UnicodeString) + len(encoded)
    closed = []

    class Kernel:
        def OpenProcess(self, *args):
            return 17

        def CloseHandle(self, handle):
            closed.append(handle)

    class Query:
        def __call__(self, handle, info_class, buffer, length, needed):
            assert info_class == 60
            needed._obj.value = size
            if buffer is None:
                return -1
            value = UnicodeString.from_buffer(buffer)
            value.length = value.maximum_length = len(encoded)
            value.buffer = ctypes.addressof(buffer) + ctypes.sizeof(UnicodeString)
            ctypes.memmove(value.buffer, encoded, len(encoded))
            if bad_pointer:
                value.buffer += size
            return 0

    class Ntdll:
        NtQueryInformationProcess = Query()

    monkeypatch.setattr(census.procs, "_win_kernel32", Kernel)
    monkeypatch.setattr(ctypes, "WinDLL", lambda *args, **kwargs: Ntdll(), raising=False)
    result = census.command_line(101)
    assert result == (None if bad_pointer else encoded.decode("utf-16-le"))
    assert closed == [17]


@pytest.mark.parametrize("command,expected", [
    ('"C:\\Program Files\\Python\\python.exe" -m agent_worktrees status-monitor --passive', True),
    ("python -m agent_worktrees status-monitor-restart", False),
    ("python -m another_package status-monitor", False),
    ("python -m agent_worktrees status", False),
])
def test_exact_monitor_command(command, expected):
    assert status_monitor_cutover._is_status_monitor_cmdline(command) is expected


@pytest.mark.skipif(os.name != "nt", reason="native Windows query")
def test_native_command_line_reads_current_process_without_shell(monkeypatch):
    def no_shell(*args, **kwargs):
        raise AssertionError("native census must not spawn subprocesses")

    monkeypatch.setattr(subprocess, "run", no_shell)
    assert census.command_line(os.getpid())
    assert any(row["pid"] == os.getpid() for row in census.snapshot())
