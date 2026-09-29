"""Tests for `headed_launch` -- the deliberate, narrow exception that opens
a VISIBLE new terminal window (``copilot --headed``), unlike every other
process-spawning helper in this codebase which exists to suppress one.
"""

from __future__ import annotations

import subprocess

import pytest

from agent_worktrees import headed_launch as hl


# ---------------------------------------------------------------------------
# psmux version gate (ban, don't work around, #3.3.6)
# ---------------------------------------------------------------------------

def _fake_run(stdout: str):
    def _run(argv, capture_output, text, timeout):
        return subprocess.CompletedProcess(argv, 0, stdout=stdout, stderr="")
    return _run


def test_check_psmux_version_allows_a_good_version(monkeypatch):
    monkeypatch.setattr(
        hl.subprocess, "run", _fake_run("tmux 3.3.8\npsmux 3.3.8 (66cf613 2026-08-18)\n")
    )
    hl.check_psmux_version("psmux")  # must not raise


def test_check_psmux_version_blocks_the_banned_version(monkeypatch):
    monkeypatch.setattr(
        hl.subprocess, "run", _fake_run("tmux 3.3.6\npsmux 3.3.6 (deadbeef 2026-01-01)\n")
    )
    with pytest.raises(hl.HeadedLaunchError, match="3.3.6"):
        hl.check_psmux_version("psmux")


def test_check_psmux_version_blocks_below_minimum(monkeypatch):
    monkeypatch.setattr(
        hl.subprocess, "run", _fake_run("tmux 3.3.4\npsmux 3.3.4 (aaaaaaa 2025-01-01)\n")
    )
    with pytest.raises(hl.HeadedLaunchError):
        hl.check_psmux_version("psmux")


def test_check_psmux_version_is_a_noop_for_tmux(monkeypatch):
    def boom(*a, **kw):
        raise AssertionError("must not shell out for tmux -- this policy is psmux-only")

    monkeypatch.setattr(hl.subprocess, "run", boom)
    hl.check_psmux_version("tmux")  # must not raise, must not even probe


def test_check_psmux_version_never_raises_when_version_cant_be_determined(monkeypatch):
    def _run(argv, capture_output, text, timeout):
        raise OSError("psmux not found")

    monkeypatch.setattr(hl.subprocess, "run", _run)
    hl.check_psmux_version("psmux")  # best-effort: absence is not itself a refusal


def test_check_psmux_version_never_raises_on_unparsable_output(monkeypatch):
    monkeypatch.setattr(hl.subprocess, "run", _fake_run("garbage, no version here\n"))
    hl.check_psmux_version("psmux")


# ---------------------------------------------------------------------------
# spawn_headed_attach -- platform dispatch (subprocess.Popen mocked; nothing
# real ever spawned)
# ---------------------------------------------------------------------------

class _FakeProc:
    def __init__(self, pid):
        self.pid = pid


def test_windows_prefers_wt_exe(monkeypatch):
    monkeypatch.setattr(hl.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hl, "check_psmux_version", lambda mux_bin: None)
    monkeypatch.setattr(hl.shutil, "which", lambda name: r"C:\wt.exe" if "wt" in name else None)
    calls = []
    monkeypatch.setattr(hl.subprocess, "Popen", lambda argv, **kw: calls.append(argv) or _FakeProc(111))

    result = hl.spawn_headed_attach("psmux", "wt-abc", title="abc")

    assert result == {"spawner": "wt.exe", "pid": 111}
    assert calls[0][0] == r"C:\wt.exe"
    assert "-w" in calls[0] and "-1" in calls[0]
    assert calls[0][-4:] == ["psmux", "attach-session", "-t", "wt-abc"]


def test_windows_falls_back_to_new_console_without_wt(monkeypatch):
    monkeypatch.setattr(hl.platform, "system", lambda: "Windows")
    monkeypatch.setattr(hl, "check_psmux_version", lambda mux_bin: None)
    monkeypatch.setattr(hl.shutil, "which", lambda name: None)
    calls = []

    def fake_popen(argv, **kwargs):
        calls.append((argv, kwargs))
        return _FakeProc(222)

    monkeypatch.setattr(hl.subprocess, "Popen", fake_popen)

    result = hl.spawn_headed_attach("psmux", "wt-abc")

    assert result["pid"] == 222
    assert "conhost" in result["spawner"]
    argv, kwargs = calls[0]
    assert argv == ["psmux", "attach-session", "-t", "wt-abc"]
    assert kwargs.get("creationflags") == hl.subprocess.CREATE_NEW_CONSOLE


def test_windows_headed_launch_refuses_a_banned_psmux_version(monkeypatch):
    monkeypatch.setattr(hl.platform, "system", lambda: "Windows")
    monkeypatch.setattr(
        hl.subprocess, "run", _fake_run("tmux 3.3.6\npsmux 3.3.6 (deadbeef 2026-01-01)\n")
    )

    def boom(*a, **kw):
        raise AssertionError("must refuse before ever spawning a window")

    monkeypatch.setattr(hl.subprocess, "Popen", boom)

    with pytest.raises(hl.HeadedLaunchError, match="3.3.6"):
        hl.spawn_headed_attach("psmux", "wt-abc")


def test_posix_probes_terminals_in_order(monkeypatch):
    monkeypatch.setattr(hl.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hl, "check_psmux_version", lambda mux_bin: None)
    # Only the third-preferred terminal ("konsole") is "installed".
    monkeypatch.setattr(hl.shutil, "which", lambda name: "/usr/bin/konsole" if name == "konsole" else None)
    calls = []
    monkeypatch.setattr(hl.subprocess, "Popen", lambda argv, **kw: calls.append(argv) or _FakeProc(333))

    result = hl.spawn_headed_attach("tmux", "wt-abc", title="my-title")

    assert result == {"spawner": "konsole", "pid": 333}
    assert calls[0][0] == "/usr/bin/konsole"
    assert calls[0][-4:] == ["tmux", "attach-session", "-t", "wt-abc"]


def test_posix_fails_closed_when_nothing_is_found(monkeypatch):
    monkeypatch.setattr(hl.platform, "system", lambda: "Linux")
    monkeypatch.setattr(hl, "check_psmux_version", lambda mux_bin: None)
    monkeypatch.setattr(hl.shutil, "which", lambda name: None)

    def boom(*a, **kw):
        raise AssertionError("must not spawn anything when no spawner was found")

    monkeypatch.setattr(hl.subprocess, "Popen", boom)

    with pytest.raises(hl.HeadedLaunchError, match="no visible terminal spawner"):
        hl.spawn_headed_attach("tmux", "wt-abc")


def test_macos_uses_osascript(monkeypatch):
    monkeypatch.setattr(hl.platform, "system", lambda: "Darwin")
    monkeypatch.setattr(hl, "check_psmux_version", lambda mux_bin: None)
    calls = []
    monkeypatch.setattr(hl.subprocess, "Popen", lambda argv, **kw: calls.append(argv) or _FakeProc(444))

    result = hl.spawn_headed_attach("tmux", "wt-abc")

    assert result == {"spawner": "osascript (Terminal.app)", "pid": 444}
    assert calls[0][0] == "osascript"
    assert "tmux attach-session -t wt-abc" in calls[0][-1]
