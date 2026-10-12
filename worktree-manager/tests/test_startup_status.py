from __future__ import annotations

from types import SimpleNamespace
import os
import subprocess
import sys

import pytest

from worktree_manager import startup_status
from worktree_manager.prereqs import PrereqStatus


@pytest.fixture
def observed(monkeypatch):
    state = {
        "manager": SimpleNamespace(installed=True, installed_version="1.2.3"),
        "core": SimpleNamespace(installed=True, state="installed"),
        "prereqs": [],
        "engine": True,
        "projects": [object()],
    }
    monkeypatch.setattr(startup_status, "self_status", lambda: state["manager"])
    monkeypatch.setattr(startup_status, "core_status", lambda: state["core"])
    monkeypatch.setattr(startup_status, "detect_baseline", lambda: state["prereqs"])
    monkeypatch.setattr(startup_status, "engine_available", lambda: state["engine"])
    monkeypatch.setattr(startup_status, "build_projects", lambda: state["projects"])
    return state


def test_complete_install_does_not_repeat_first_install_guide(observed, capsys):
    startup_status.print_startup_status()
    text = capsys.readouterr().out
    assert "Installed Manager version: 1.2.3" in text
    assert "Agent-worktrees core: installed" in text
    assert "picker <project>" in text
    assert "setup" not in text
    assert "self-install" not in text
    assert "Phase 2" not in text
    assert "not a full health or authentication check" in text


def test_absent_core_and_missing_prereq_show_actual_remaining_plan(observed, capsys):
    observed["core"] = SimpleNamespace(installed=False, state="absent")
    observed["prereqs"] = [PrereqStatus("uv", present=False)]
    startup_status.print_startup_status()
    text = capsys.readouterr().out
    assert "core: absent" in text
    assert "Baseline prerequisite gaps: uv" in text
    assert "setup" in text
    assert "picker <project>" not in text


def test_absent_manager_does_not_hide_observed_core_state(observed, capsys):
    observed["manager"] = SimpleNamespace(installed=False, installed_version=None)
    startup_status.print_startup_status()
    text = capsys.readouterr().out
    assert "self-install --apply" in text
    assert "core: installed" in text


def test_damaged_manager_with_marker_and_stub_still_shows_repair(observed, capsys):
    observed["manager"] = SimpleNamespace(installed=False, installed_version="1.2.3")
    startup_status.print_startup_status()
    text = capsys.readouterr().out
    assert "Installed Manager version: 1.2.3" in text
    assert "incomplete or absent" in text
    assert "self-install --apply" in text


def test_missing_engine_does_not_claim_launch_readiness(observed, capsys):
    observed["engine"] = False
    startup_status.print_startup_status()
    text = capsys.readouterr().out
    assert "doctor" in text
    assert "picker <project>" not in text


def test_no_projects_is_not_a_missing_runtime(observed, capsys):
    observed["projects"] = []
    startup_status.print_startup_status()
    text = capsys.readouterr().out
    assert "No registered projects were reported" in text
    assert "setup" not in text


def test_observations_refresh_on_every_invocation(observed, capsys):
    observed["core"] = SimpleNamespace(installed=False, state="partial")
    startup_status.print_startup_status()
    assert "setup" in capsys.readouterr().out
    observed["core"] = SimpleNamespace(installed=True, state="installed")
    startup_status.print_startup_status()
    assert "setup" not in capsys.readouterr().out


def test_real_bare_cli_reads_an_isolated_install_snapshot(tmp_path):
    environment = dict(os.environ)
    environment.update(
        USERPROFILE=str(tmp_path), HOME=str(tmp_path),
        WORKTREE_MANAGER_ROOT=str(tmp_path / "manager"),
    )
    for key in (
        "AGENT_WORKTREES_ENGINE_COMMAND", "AGENT_WORKTREES_ENGINE_INSTALL_ROOT",
        "AGENT_WORKTREES_ENGINE_VERSION", "AGENT_WORKTREES_INSTALL_DIR",
        "COPILOT_PLUGIN_ROOT",
    ):
        environment.pop(key, None)
    result = subprocess.run(
        [sys.executable, "-m", "worktree_manager"],
        cwd=tmp_path, env=environment, capture_output=True, text=True,
        timeout=60, check=False,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stderr
    assert "Running version:" in result.stdout
    assert "Agent-worktrees core:" in result.stdout
    assert "Registered projects reported: 0" in result.stdout
    assert "Build-out roadmap" not in result.stdout
    assert "you are here" not in result.stdout
    assert not list(tmp_path.iterdir())
