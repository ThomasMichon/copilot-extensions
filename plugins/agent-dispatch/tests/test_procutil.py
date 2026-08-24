"""Tests for the shared no-console-window spawn helper."""

from __future__ import annotations

import subprocess
from pathlib import Path

from agent_dispatch import procutil


def test_no_window_kwargs_on_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "nt")
    kw = procutil.no_window_kwargs()
    expected = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    assert kw == {"creationflags": expected}


def test_no_window_kwargs_off_windows(monkeypatch):
    monkeypatch.setattr(procutil.os, "name", "posix")
    assert procutil.no_window_kwargs() == {}


def test_agent_worktrees_capture_bypasses_cmd_and_detaches_on_windows(
    tmp_path, monkeypatch
):
    runtime = tmp_path / ".agent-worktrees"
    python = _make_slot(runtime, "1.5.3-dev9")
    pythonw = python.with_name("pythonw.exe")
    pythonw.write_text("")
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
        str(pythonw), "-m", "agent_worktrees", "get", "machine"
    ]
    assert not any(arg.lower().endswith((".cmd", ".bat")) for arg in captured["cmd"])
    flags = captured["kwargs"]["creationflags"]
    assert flags & 0x00000008  # DETACHED_PROCESS
    assert not (flags & 0x08000000)  # CREATE_NO_WINDOW
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
