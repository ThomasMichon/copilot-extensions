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
