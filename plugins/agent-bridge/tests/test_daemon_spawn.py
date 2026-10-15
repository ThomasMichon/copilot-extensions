"""Bridge daemons launch through the shared ``spawn_daemon`` primitive.

Both the service (``_spawn_detached_argv``) and a deploy's passive generation
start through ``_launch_bridge_daemon``: rooted at the absolute install dir,
stdout in ``agent-bridge.log`` and stderr in ``agent-bridge-err.log``, with a
hidden console for console-subsystem descendants and Job breakaway.
"""

from __future__ import annotations

import os
import subprocess
from unittest.mock import Mock

import pytest

import agent_procutil
from agent_bridge import __main__ as main
from agent_bridge.install_paths import CONFIG_DIR_ENV, INSTALL_DIR_ENV


@pytest.fixture()
def recorded_spawn(monkeypatch, tmp_path):
    calls: list = []

    def spawn(argv, **kwargs):
        calls.append((list(argv), kwargs))
        return Mock(pid=4242)

    monkeypatch.setattr(main, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(main, "spawn_daemon", spawn)
    monkeypatch.delenv(INSTALL_DIR_ENV, raising=False)
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)
    return calls


def test_service_launch_uses_spawn_daemon_with_split_logs(recorded_spawn, tmp_path):
    main._spawn_detached_argv(["py", "-m", "agent_bridge", "start"])

    (argv, kwargs), = recorded_spawn
    assert argv == ["py", "-m", "agent_bridge", "start"]
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["log_path"] == os.path.join(str(tmp_path), "agent-bridge.log")
    assert kwargs["stderr_log_path"] == os.path.join(str(tmp_path), "agent-bridge-err.log")
    assert kwargs["console_children"] is True
    assert kwargs.get("breakaway", True) is True
    assert kwargs["env_overrides"] == {}


def test_passive_generation_uses_the_same_launch(recorded_spawn, tmp_path):
    handle = main._launch_bridge_daemon(
        ["py", "-m", "agent_bridge", "start", "--passive"],
        display="agent-bridge passive generation",
        contained_last_resort=False,
    )

    assert handle.pid == 4242
    (_, kwargs), = recorded_spawn
    assert kwargs["cwd"] == str(tmp_path)
    assert kwargs["stderr_log_path"].endswith("agent-bridge-err.log")


def test_relative_install_root_is_anchored(recorded_spawn, monkeypatch, tmp_path):
    monkeypatch.chdir(tmp_path)
    (tmp_path / "rel").mkdir()
    monkeypatch.setattr(main, "_INSTALL_DIR", "rel")
    monkeypatch.setenv(INSTALL_DIR_ENV, "rel")
    monkeypatch.setenv(CONFIG_DIR_ENV, os.path.join("rel", "elevated"))

    main._spawn_detached_argv(["py"])

    (_, kwargs), = recorded_spawn
    root = str(tmp_path / "rel")
    assert kwargs["cwd"] == root
    assert kwargs["log_path"] == os.path.join(root, "agent-bridge.log")
    assert kwargs["env_overrides"] == {
        INSTALL_DIR_ENV: root,
        CONFIG_DIR_ENV: os.path.join(root, "elevated"),
    }


def test_missing_install_root_is_created(recorded_spawn, monkeypatch, tmp_path):
    root = tmp_path / "fresh"
    monkeypatch.setattr(main, "_INSTALL_DIR", str(root))
    main._spawn_detached_argv(["py"])
    assert root.is_dir()


def test_real_launch_splits_logs(monkeypatch, tmp_path):
    """End to end through the real primitive (with a recording Popen)."""
    seen: list = []

    def popen(argv, **kwargs):
        seen.append(kwargs)
        return Mock(pid=1)

    monkeypatch.setattr(main, "_INSTALL_DIR", str(tmp_path))
    monkeypatch.setattr(agent_procutil.subprocess, "Popen", popen)
    monkeypatch.delenv("COPILOT_EXTENSIONS_TEST_CONTAINED", raising=False)
    monkeypatch.delenv(INSTALL_DIR_ENV, raising=False)
    monkeypatch.delenv(CONFIG_DIR_ENV, raising=False)

    main._spawn_detached_argv(["py", "-m", "agent_bridge", "start"])

    (kwargs,) = seen
    assert kwargs["stdin"] is subprocess.DEVNULL
    assert kwargs["stdout"].name == str(tmp_path / "agent-bridge.log")
    assert kwargs["stderr"].name == str(tmp_path / "agent-bridge-err.log")
    assert kwargs["cwd"] == str(tmp_path)
    for name in ("agent-bridge.log", "agent-bridge-err.log"):
        assert "display=agent-bridge service" in (tmp_path / name).read_text(encoding="utf-8")
