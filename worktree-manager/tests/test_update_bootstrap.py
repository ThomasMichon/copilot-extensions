"""Fresh bootstrap repair precedes, and does not replace, suite orchestration."""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from worktree_manager import __main__ as manager
from worktree_manager import core_install, update_bootstrap


def test_update_bootstrap_delivery_paths_are_sandboxed():
    home = Path(os.environ["USERPROFILE"])
    assert home == Path.home() and home.name == "fake-home"
    assert "WORKTREE_MANAGER_ROOT" not in os.environ
    assert "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR" not in os.environ


@pytest.mark.parametrize("rc", [0, 17])
def test_update_bootstrap_repairs_before_orchestration(monkeypatch, rc):
    calls = []
    monkeypatch.setattr(
        update_bootstrap, "repair_engine", lambda source: calls.append("repair") or rc,
    )
    monkeypatch.setattr(
        manager, "_cmd_update_steps",
        lambda rest, run, **kwargs: calls.append(rest) or 5,
    )
    flags = ["--project", "sample", "--force", "--skip-modules", "a b"]
    assert manager._cmd_update(["--repair-engine", "/fresh/source", *flags]) == (rc or 5)
    assert calls == (["repair"] if rc else ["repair", flags])


def test_update_bootstrap_uses_supported_fresh_installer(monkeypatch, tmp_path):
    root = tmp_path / "manager"
    script = root / "staging/plugins/agent-worktrees/scripts/install.sh"
    script.parent.mkdir(parents=True)
    script.write_text("#!/bin/sh\nexit 19\n")
    monkeypatch.setattr(core_install, "install_script", lambda path: script)
    calls = []

    def run(command, **kwargs):
        calls.append((command, kwargs))
        return subprocess.CompletedProcess(command, 19)

    monkeypatch.setattr(update_bootstrap.subprocess, "run", run)
    monkeypatch.setattr(update_bootstrap.os, "name", "posix")
    assert update_bootstrap.repair_engine(root / "staging") == 19
    command, options = calls[0]
    assert command == ["bash", str(script), "update"]
    assert not options.get("start_new_session", False)
    assert not options.get("close_fds", False)
    assert options["env"]["PATH"]


def test_update_bootstrap_missing_installer_is_failure(monkeypatch, capsys):
    monkeypatch.setattr(core_install, "install_script", lambda path: None)
    assert update_bootstrap.repair_engine(Path("/missing/fresh-source")) == 1
    assert "unavailable" in capsys.readouterr().err


def test_update_bootstrap_does_not_route_ordinary_update_through_repair(monkeypatch):
    monkeypatch.setattr(
        update_bootstrap, "repair_engine",
        lambda source: pytest.fail("ordinary Manager update must not require bootstrap staging"),
    )
    monkeypatch.setattr(manager, "_cmd_update_steps", lambda rest, run: 5)
    assert manager._cmd_update(["--force"]) == 5


def test_update_bootstrap_fresh_manager_does_not_reselect_source(monkeypatch):
    from worktree_manager import engine_client, self_install

    monkeypatch.setattr(
        update_bootstrap, "repair_engine", lambda source: 0,
    )
    monkeypatch.setattr(
        self_install, "self_update",
        lambda **kwargs: pytest.fail("fresh Manager must not fetch a second source"),
    )
    calls = []
    monkeypatch.setattr(
        engine_client, "run_engine_passthrough",
        lambda project, args: calls.append((project, args)) or 0,
    )
    assert manager._cmd_update([
        "--repair-engine", "/fresh/source", "--project", "sample", "--force",
    ]) == 0
    assert calls == [("sample", ["update", "--no-manager", "--force"])]
