"""Cheap OS identity must not enter management-provider discovery at startup."""

from __future__ import annotations

import io
import os
import platform
import subprocess
import sys
from pathlib import Path

import pytest
import yaml
from agent_procutil import no_window_kwargs

from agent_worktrees import config, front_door_cli, git_ops, installer, registry_paths

pytestmark = pytest.mark.guard


@pytest.fixture
def forbid_platform_discovery(monkeypatch):
    def unexpected():
        raise AssertionError("OS branching must not query platform/uname/WMI")

    monkeypatch.setattr(platform, "system", unexpected)
    monkeypatch.setattr(platform, "uname", unexpected)


@pytest.mark.parametrize("platform_id", ["win32", "linux", "darwin"])
def test_home_and_registry_roots_use_interpreter_platform(
    tmp_path, monkeypatch, forbid_platform_discovery, platform_id,
):
    monkeypatch.setattr(sys, "platform", platform_id)
    monkeypatch.delenv("AGENT_HOME", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    fallback = tmp_path / "home"
    profile = tmp_path / "profile"
    monkeypatch.setattr(Path, "home", classmethod(lambda cls: fallback))
    monkeypatch.setenv("USERPROFILE", str(profile))
    expected = profile if platform_id == "win32" else fallback
    assert config._home() == expected
    assert config.install_dir() == expected / ".agent-worktrees"
    assert registry_paths.registry_root() == expected / ".agent-worktrees"
    assert installer.projects_yaml_path() == expected / ".agent-worktrees" / "projects.yaml"
    mixed_path = str((tmp_path / "MixedCase").resolve()).rstrip("/\\")
    assert git_ops._normalize_wt_path(mixed_path) == (
        mixed_path.lower() if platform_id == "win32" else mixed_path
    )
    monkeypatch.delenv("USERPROFILE")
    assert config._home() == fallback
    assert registry_paths.registry_root() == fallback / ".agent-worktrees"
    monkeypatch.setenv("AGENT_HOME", str(profile))
    assert config._home() == profile
    assert registry_paths.registry_root() == profile / ".agent-worktrees"


@pytest.mark.parametrize(
    ("platform_id", "proc_version", "expected"),
    [("win32", "", "windows"), ("linux", "Linux generic", "linux"),
     ("linux", "Linux microsoft-standard", "wsl")],
)
def test_platform_detection_preserves_windows_linux_and_wsl(
    monkeypatch, forbid_platform_discovery, platform_id, proc_version, expected,
):
    monkeypatch.setattr(sys, "platform", platform_id)
    real_open = open

    def read_version(path, *args, **kwargs):
        if path == "/proc/version":
            return io.StringIO(proc_version)
        return real_open(path, *args, **kwargs)

    monkeypatch.setattr("builtins.open", read_version)
    assert config.detect_platform() == expected


@pytest.mark.parametrize("platform_id", ["win32", "linux"])
def test_runtime_python_path_uses_interpreter_platform(
    tmp_path, monkeypatch, forbid_platform_discovery, platform_id,
):
    monkeypatch.setattr(sys, "platform", platform_id)
    monkeypatch.setenv("AGENT_HOME", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    root = tmp_path / ".agent-worktrees"
    parts = ("Scripts", "python.exe") if platform_id == "win32" else ("bin", "python")
    executable = (root / "versions" / "fixture-version").joinpath(*parts)
    executable.parent.mkdir(parents=True)
    executable.touch()
    (root / "current-version").write_text("fixture-version", encoding="utf-8")
    assert config.venv_python() == executable


def test_native_cli_project_configuration_never_queries_uname(tmp_path):
    home = tmp_path / "home"
    anchor = tmp_path / "repo"
    anchor.mkdir()
    subprocess.run(
        ["git", "init", "-q", str(anchor)], check=True, timeout=10,
        capture_output=True, **no_window_kwargs(),
    )
    state = home / ".fixture"
    state.mkdir(parents=True)
    expected_platform = "windows" if sys.platform == "win32" else "linux"
    (state / "config.yaml").write_text(yaml.safe_dump({
        "machine": "fixture-machine", "platform": expected_platform,
        "srcroot": str(tmp_path), "repo_name": "fixture",
        "repos": {"fixture": {"anchor": str(anchor), "worktree_root": str(tmp_path / "worktrees")}},
    }), encoding="utf-8")
    registry = home / ".agent-worktrees"
    registry.mkdir()
    (registry / "projects.yaml").write_text(yaml.safe_dump({
        "schema_version": 2, "projects": {"fixture": {"anchor": str(anchor)}},
    }), encoding="utf-8")
    env = dict(os.environ)
    for name in ("AGENT_HOME", "COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT",
                 "AGENT_WORKTREES_PAYLOAD_ROOT"):
        env.pop(name, None)
    env.update(HOME=str(home), USERPROFILE=str(home))
    script = """
import platform, runpy, sys
# Python 3.12 POSIX uuid initialization probes the OS, not Windows WMI.
# Warm only that stdlib dependency; product imports and config stay fresh.
import uuid
class UnexpectedDiscovery(BaseException):
    pass
def forbidden():
    raise UnexpectedDiscovery("CLI startup queried platform/uname/WMI")
platform.system = forbidden
platform.uname = forbidden
sys.argv = ["agent-worktrees", "--project", "fixture", "get", "platform"]
runpy.run_module("agent_worktrees", run_name="__main__")
"""
    completed = subprocess.run(
        [sys.executable, "-c", script], cwd=anchor, env=env,
        capture_output=True, text=True, timeout=20, **no_window_kwargs(),
    )
    assert completed.returncode == 0, completed.stderr
    assert completed.stdout.strip() == expected_platform


def test_front_door_git_probe_forwards_shared_headless_kwargs(tmp_path, monkeypatch):
    seen = {}
    marker = {"creationflags": 1234}

    def run(argv, **kwargs):
        seen.update(kwargs)
        return subprocess.CompletedProcess(argv, 0, str(tmp_path), "")

    monkeypatch.setattr(front_door_cli, "no_window_kwargs", lambda: marker)
    monkeypatch.setattr(front_door_cli.subprocess, "run", run)
    monkeypatch.setattr(git_ops, "resolve_to_anchor", lambda path: path)
    assert front_door_cli._git_toplevel(tmp_path) == tmp_path.resolve()
    assert seen["creationflags"] == marker["creationflags"]
