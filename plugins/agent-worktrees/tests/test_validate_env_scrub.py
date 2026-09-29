"""Tests for agent_worktrees.validate's subprocess env scrub (#4552)."""

from __future__ import annotations

from pathlib import Path

from agent_worktrees import env_scrub, validate

_LEAKED_ENV_VARS = env_scrub._PYTHON_RUNTIME_ENV


def _leak_python_runtime_env(monkeypatch):
    for name in _LEAKED_ENV_VARS:
        monkeypatch.setenv(name, r"C:\fake\stale\value")


def _capture_env(monkeypatch):
    """Patch ``validate.subprocess.run`` and return a dict the first call's
    ``env`` kwarg lands in."""
    captured = {}

    def fake_run(cmd, **kw):
        captured["env"] = kw.get("env")
        import subprocess as _sp
        return _sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(validate.subprocess, "run", fake_run)
    return captured


def _capture_all_envs(monkeypatch):
    """Patch ``validate.subprocess.run`` and return a list of every call's
    ``env`` kwarg, in call order (``_check_python`` makes two: py_compile
    then ruff)."""
    captured: list[dict[str, str] | None] = []

    def fake_run(cmd, **kw):
        captured.append(kw.get("env"))
        import subprocess as _sp
        return _sp.CompletedProcess(cmd, 0, "", "")

    monkeypatch.setattr(validate.subprocess, "run", fake_run)
    return captured


def test_check_python_scrubs_python_runtime_env_for_both_spawns(
    tmp_path: Path, monkeypatch,
):
    """``_check_python`` spawns twice (``py_compile`` then ``ruff``); both
    must get the scrubbed environment, not just the first."""
    _leak_python_runtime_env(monkeypatch)
    envs = _capture_all_envs(monkeypatch)
    validate._check_python(tmp_path / "f.py")
    assert len(envs) == 2, "expected both the py_compile and ruff spawns"
    for env in envs:
        for name in _LEAKED_ENV_VARS:
            assert name not in env


def test_check_bash_scrubs_python_runtime_env(tmp_path: Path, monkeypatch):
    _leak_python_runtime_env(monkeypatch)
    captured = _capture_env(monkeypatch)
    validate._check_bash(tmp_path / "f.sh")
    for name in _LEAKED_ENV_VARS:
        assert name not in captured["env"]


def test_check_powershell_scrubs_python_runtime_env(tmp_path: Path, monkeypatch):
    _leak_python_runtime_env(monkeypatch)
    captured = _capture_env(monkeypatch)
    validate._check_powershell(tmp_path / "f.ps1")
    for name in _LEAKED_ENV_VARS:
        assert name not in captured["env"]
