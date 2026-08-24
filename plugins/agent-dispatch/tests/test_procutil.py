"""Tests for the shared no-console-window spawn helper."""

from __future__ import annotations

import ctypes
import shutil
import subprocess
import sys
import threading
import time
from ctypes import wintypes
from pathlib import Path

import pytest

from agent_dispatch import procutil


def test_no_window_kwargs_on_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "nt")
    kw = procutil.no_window_kwargs()
    expected = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert kw == {"creationflags": expected}


def test_no_window_kwargs_off_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "posix")
    assert procutil.no_window_kwargs() == {}


def test_agent_worktrees_capture_bypasses_cmd_with_no_window_console_tree(
    tmp_path, monkeypatch
):
    runtime = tmp_path / ".agent-worktrees"
    python = _make_slot(runtime, "1.5.3-dev9")
    (runtime / "current-version").write_text("1.5.3-dev9")
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "host-a\n", "")

    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "nt")
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _n: r"C:\bin\agent-worktrees.CMD"
    )
    monkeypatch.setattr(procutil.subprocess, "run", fake_run)
    result = procutil.run_agent_worktrees_capture("get", "machine", timeout=15)

    assert result is not None and result.stdout == "host-a\n"
    assert captured["cmd"] == [
        str(python), "-m", "agent_worktrees", "get", "machine"
    ]
    assert not any(arg.lower().endswith((".cmd", ".bat")) for arg in captured["cmd"])
    flags = captured["kwargs"]["creationflags"]
    assert flags == getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert not (flags & 0x00000008)  # DETACHED_PROCESS would free descendants
    assert captured["kwargs"]["capture_output"] is True
    assert captured["kwargs"]["stdin"] is subprocess.DEVNULL


def test_agent_worktrees_launch_has_no_windows_path_fallback(tmp_path, monkeypatch):
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: tmp_path))
    monkeypatch.setattr(procutil.os, "name", "nt")
    monkeypatch.setattr(
        procutil.shutil, "which", lambda _n: r"C:\bin\agent-worktrees.CMD"
    )
    assert procutil.agent_worktrees_launch_prefix() is None


def test_agent_worktrees_capture_preserves_posix_process_semantics(monkeypatch):
    captured = {}

    def fake_run(cmd, **kwargs):
        captured["cmd"] = cmd
        captured["kwargs"] = kwargs
        return subprocess.CompletedProcess(cmd, 0, "host-a\n", "")

    monkeypatch.setattr(procutil.os, "name", "posix")
    monkeypatch.setattr(
        procutil, "agent_worktrees_launch_prefix",
        lambda: ["/usr/bin/agent-worktrees"],
    )
    monkeypatch.setattr(procutil.subprocess, "run", fake_run)

    procutil.run_agent_worktrees_capture("get", "machine", timeout=15)

    assert captured["cmd"] == ["/usr/bin/agent-worktrees", "get", "machine"]
    assert "creationflags" not in captured["kwargs"]
    assert "start_new_session" not in captured["kwargs"]


def test_background_capture_preserves_timeout_failure(monkeypatch):
    monkeypatch.setattr(
        procutil.subprocess,
        "run",
        lambda *_a, **_k: (_ for _ in ()).throw(
            subprocess.TimeoutExpired("probe", 3)
        ),
    )
    assert procutil.run_background_capture(["probe"], timeout=3) is None


@pytest.mark.skipif(sys.platform != "win32", reason="Windows console integration")
def test_background_capture_keeps_console_descendants_off_default_terminal():
    """A captured console root must also suppress its real console descendants."""
    git = shutil.which("git")
    if git is None:
        pytest.skip("git console executable is unavailable")

    class ProcessEntry(ctypes.Structure):
        _fields_ = [
            ("dwSize", wintypes.DWORD),
            ("cntUsage", wintypes.DWORD),
            ("th32ProcessID", wintypes.DWORD),
            ("th32DefaultHeapID", ctypes.c_size_t),
            ("th32ModuleID", wintypes.DWORD),
            ("cntThreads", wintypes.DWORD),
            ("th32ParentProcessID", wintypes.DWORD),
            ("pcPriClassBase", wintypes.LONG),
            ("dwFlags", wintypes.DWORD),
            ("szExeFile", wintypes.WCHAR * 260),
        ]

    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    user32 = ctypes.WinDLL("user32", use_last_error=True)
    kernel32.CreateToolhelp32Snapshot.restype = wintypes.HANDLE

    def process_snapshot() -> dict[int, str]:
        snapshot = kernel32.CreateToolhelp32Snapshot(0x00000002, 0)
        if snapshot == wintypes.HANDLE(-1).value:
            return {}
        found: dict[int, str] = {}
        try:
            entry = ProcessEntry()
            entry.dwSize = ctypes.sizeof(entry)
            ok = kernel32.Process32FirstW(snapshot, ctypes.byref(entry))
            while ok:
                found[int(entry.th32ProcessID)] = entry.szExeFile
                ok = kernel32.Process32NextW(snapshot, ctypes.byref(entry))
        finally:
            kernel32.CloseHandle(snapshot)
        return found

    def foreground_state() -> tuple[int, str]:
        hwnd = int(user32.GetForegroundWindow())
        length = user32.GetWindowTextLengthW(hwnd)
        title = ctypes.create_unicode_buffer(length + 1)
        user32.GetWindowTextW(hwnd, title, length + 1)
        return hwnd, title.value

    child_code = (
        "import subprocess,sys\n"
        "for _ in range(8):\n"
        " subprocess.run([sys.argv[1], '--version'], check=True)\n"
        "print('nested-ok', flush=True)\n"
    )
    baseline_processes = process_snapshot()
    baseline_foreground = foreground_state()
    result: dict[str, subprocess.CompletedProcess[str] | None] = {}

    def run_probe() -> None:
        result["value"] = procutil.run_background_capture(
            [sys.executable, "-c", child_code, git], timeout=15
        )

    probe = threading.Thread(target=run_probe)
    probe.start()
    new_openconsole: set[int] = set()
    suspicious_titles: list[str] = []
    while probe.is_alive():
        new_openconsole.update(
            pid
            for pid, name in process_snapshot().items()
            if pid not in baseline_processes and name.lower() == "openconsole.exe"
        )
        state = foreground_state()
        if state != baseline_foreground and (
            "git.exe" in state[1].lower() or "cmd.exe" in state[1].lower()
        ):
            suspicious_titles.append(state[1])
        time.sleep(0.003)
    probe.join()

    completed = result["value"]
    assert completed is not None
    assert completed.returncode == 0
    assert completed.stdout.rstrip().endswith("nested-ok")
    assert completed.stderr == ""
    assert new_openconsole == set()
    assert suspicious_titles == []


def test_runtime_root_is_under_home_not_payload():
    root = procutil.runtime_root()
    assert root == Path.home() / ".agent-dispatch"
    # The runtime root must never be inside the Copilot plugin payload tree.
    assert "installed-plugins" not in root.parts


def test_relocate_off_payload_chdirs_to_runtime_root(tmp_path, monkeypatch):
    # Simulate a daemon lazy-started with the plugin payload as its CWD.
    payload = tmp_path / ".copilot" / "installed-plugins" / "x" / "agent-dispatch"
    payload.mkdir(parents=True)
    fake_home = tmp_path / "home"
    fake_home.mkdir()
    monkeypatch.setattr(procutil.Path, "home", classmethod(lambda cls: fake_home))
    monkeypatch.chdir(payload)
    assert Path.cwd() == payload

    procutil.relocate_off_payload()

    # It relocated OFF the payload to the runtime root (which it created).
    assert Path.cwd() == fake_home / ".agent-dispatch"
    assert "installed-plugins" not in Path.cwd().parts


def test_relocate_off_payload_is_best_effort(monkeypatch):
    # A chdir failure must never be fatal (the daemon still starts).
    monkeypatch.setattr(procutil.os, "chdir", lambda *_a, **_k: (_ for _ in ()).throw(OSError("boom")))
    procutil.relocate_off_payload()  # does not raise


# -- resolve_runtime_python (the standardized versioned-runtime resolver) -----


def _make_slot(root: Path, version: str, *, complete: bool = False) -> Path:
    """Create a ``versions/<version>`` slot with a fake interpreter; return it."""
    sub = "Scripts/python.exe" if procutil.os.name == "nt" else "bin/python"
    py = root / "versions" / version / sub
    py.parent.mkdir(parents=True)
    py.write_text("")
    if complete:
        (root / "versions" / version / ".install-complete.json").write_text("{}")
    return py


def test_resolve_runtime_python_tier1_current_version_marker(tmp_path):
    root = tmp_path / ".agent-bridge"
    py = _make_slot(root, "0.1.0-dev9")
    _make_slot(root, "0.1.0-dev99")  # newer slot exists but marker wins
    (root / "current-version").write_text("0.1.0-dev9")
    assert procutil.resolve_runtime_python(root) == py


def test_resolve_runtime_python_tier2_last_known_good(tmp_path):
    root = tmp_path / ".agent-bridge"
    py = _make_slot(root, "0.1.0-dev9")
    (root / "last-known-good").write_text("0.1.0-dev9")  # marker absent -> LKG
    assert procutil.resolve_runtime_python(root) == py


def test_resolve_runtime_python_tier3_prefers_newest_complete_slot(tmp_path):
    root = tmp_path / ".agent-worktrees"
    _make_slot(root, "1.5.3-dev50", complete=True)
    py_new = _make_slot(root, "1.5.3-dev185", complete=True)
    _make_slot(root, "1.5.3-dev200")  # newest but INCOMPLETE -> not preferred
    # No marker, no LKG: newest *complete* slot wins, numeric-aware (185 > 50).
    assert procutil.resolve_runtime_python(root) == py_new


def test_resolve_runtime_python_none_when_no_runtime(tmp_path):
    assert procutil.resolve_runtime_python(tmp_path / ".agent-bridge") is None


def test_resolve_runtime_python_ignores_venv_junction_layout(tmp_path):
    # A bare ``venv``/``.venv`` dir (the old hard-coded path) is NOT a versioned
    # slot, so it is never resolved -- the #974 regression guard.
    root = tmp_path / ".agent-bridge"
    (root / "venv" / "Scripts").mkdir(parents=True)
    (root / "venv" / "Scripts" / "python.exe").write_text("")
    assert procutil.resolve_runtime_python(root) is None
