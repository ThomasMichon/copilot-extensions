"""Tests for agent_worktrees.env_scrub (#4552)."""

from __future__ import annotations

from agent_worktrees import env_scrub


def test_scrub_removes_all_python_runtime_vars():
    env = {
        "PYTHONHOME": r"C:\fake\stale\home",
        "PYTHONPATH": r"C:\fake\stale\path",
        "PYTHONEXECUTABLE": r"C:\fake\stale\python.exe",
        "VIRTUAL_ENV": r"C:\fake\stale\venv",
        "UV_INTERNAL__PYTHONHOME": r"C:\fake\uv\internal",
        "__PYVENV_LAUNCHER__": r"C:\fake\stale\launcher.exe",
        "PATH": r"C:\Windows\System32",
    }
    result = env_scrub.scrub_python_runtime_env(env)
    for name in env_scrub._PYTHON_RUNTIME_ENV:
        assert name not in result
    assert result["PATH"] == r"C:\Windows\System32"


def test_scrub_is_noop_when_vars_absent():
    env = {"PATH": "/usr/bin"}
    result = env_scrub.scrub_python_runtime_env(env)
    assert result == {"PATH": "/usr/bin"}


def test_scrub_mutates_and_returns_same_object():
    env = {"PYTHONHOME": "x"}
    result = env_scrub.scrub_python_runtime_env(env)
    assert result is env
    assert "PYTHONHOME" not in env
