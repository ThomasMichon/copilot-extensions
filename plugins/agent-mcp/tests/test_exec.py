from __future__ import annotations

import shutil
import sys

from agent_mcp._exec import resolve_argv


def test_resolve_argv_resolves_real_executable():
    # python is on PATH; argv[0] should resolve to a full path, args untouched.
    out = resolve_argv(["python", "-c", "pass"])
    assert out[1:] == ["-c", "pass"]
    assert out[0] == shutil.which("python")
    assert out[0] != "python"  # actually resolved


def test_resolve_argv_keeps_unknown_command():
    # An unresolvable command is returned unchanged so the caller's spawn
    # raises its normal FileNotFoundError.
    argv = ["definitely-not-a-real-command-xyz", "--flag"]
    assert resolve_argv(argv) == argv


def test_resolve_argv_empty():
    assert resolve_argv([]) == []


def test_resolve_argv_does_not_mutate_input():
    original = ["python", "-c", "pass"]
    resolve_argv(original)
    assert original == ["python", "-c", "pass"]


def test_resolve_argv_resolves_sys_executable_basename():
    # sys.executable is always present; resolving its basename should find it.
    out = resolve_argv([sys.executable])
    assert out == [sys.executable] or out[0] == shutil.which(sys.executable)


# --- resolve_spawn: Windows-preference for .ps1 (arg fidelity) ---------------

from agent_mcp._exec import resolve_spawn  # noqa: E402


def test_resolve_spawn_posix_is_resolve_argv():
    # On POSIX, resolve_spawn == resolve_argv (no PowerShell wrapping).
    out = resolve_spawn(["python", "-c", "pass"], is_windows=False)
    assert out[1:] == ["-c", "pass"]
    assert out[0] == shutil.which("python")


def test_resolve_spawn_prefers_ps1_on_windows(tmp_path):
    # A .ps1 sibling on PATH is preferred and invoked via the PowerShell host,
    # with the tool's args carried through verbatim (no .cmd re-parsing).
    (tmp_path / "vei-search.ps1").write_text("param()\n", encoding="utf-8")
    (tmp_path / "vei-search.cmd").write_text("@echo off\n", encoding="utf-8")
    out = resolve_spawn(
        ["vei-search", "search", "a & b", "--limit", "5"],
        is_windows=True, path=str(tmp_path), pwsh="C:\\pwsh.exe",
    )
    assert out[0] == "C:\\pwsh.exe"
    assert "-File" in out
    assert out[out.index("-File") + 1] == str(tmp_path / "vei-search.ps1")
    # The tool args survive as distinct tokens after the script path.
    assert out[-4:] == ["search", "a & b", "--limit", "5"]


def test_resolve_spawn_falls_back_when_no_ps1(tmp_path, monkeypatch):
    # No .ps1 present -> fall back to resolve_argv (PATHEXT .cmd/.exe).
    monkeypatch.setattr("agent_mcp._exec.shutil.which", lambda n: None)
    out = resolve_spawn(["vei-search", "status"], is_windows=True,
                        path=str(tmp_path), pwsh=None)
    assert out == ["vei-search", "status"]  # unchanged (nothing resolvable)


def test_resolve_spawn_falls_back_when_no_pwsh_host(tmp_path, monkeypatch):
    # A .ps1 exists but no PowerShell host is available -> last-resort resolve_argv.
    (tmp_path / "vei-search.ps1").write_text("param()\n", encoding="utf-8")
    monkeypatch.setattr("agent_mcp._exec.shutil.which", lambda n: None)
    out = resolve_spawn(["vei-search", "status"], is_windows=True,
                        path=str(tmp_path), pwsh=None)
    # No host -> not wrapped via -File; falls through to resolve_argv (unchanged).
    assert out == ["vei-search", "status"]


def test_resolve_spawn_empty():
    assert resolve_spawn([], is_windows=True) == []
