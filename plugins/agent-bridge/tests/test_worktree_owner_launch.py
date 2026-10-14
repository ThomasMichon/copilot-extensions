"""Installed worktree-owner resolution under native service environments."""

from __future__ import annotations

import json
import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import Mock

import pytest
from agent_procutil import no_window_kwargs

from agent_bridge import acquisition_authority as authority
from agent_bridge import local_cache_refresh
from agent_bridge import session_lifecycle_cli as lifecycle
from agent_bridge._peer_launch import ContextRefused
from agent_bridge.acquisition_contract import AcquisitionRefused, AcquisitionRequest

pytestmark = pytest.mark.contract("agent_bridge.acquisition.worktree_owner")
PLUGIN = Path(__file__).resolve().parents[1]


@pytest.fixture
def installation(tmp_path, monkeypatch):
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    own = tmp_path / ".agent-bridge"
    scripts = own / "scripts"
    scripts.mkdir(parents=True)
    for name in ("versioned_runtime.py", "versioned_runtime_leases.py", "versioned_runtime_construction.py"):
        shutil.copy2(PLUGIN / "scripts" / name, scripts)
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: own)
    monkeypatch.setenv("PATH", "/usr/bin:/bin" if os.name != "nt" else "")
    which = Mock(return_value=None)
    monkeypatch.setattr(lifecycle.shutil, "which", which)
    peer = tmp_path / ".agent-worktrees"
    return own, peer, which


def complete_slot(peer, version="1.0.0", *, interpreter=True):
    slot = peer / "versions" / version
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    if interpreter:
        python.write_bytes(b"interpreter fixture")
    (slot / ".install-complete.json").write_text(
        json.dumps({"version": version, "completed_at": "2026-01-01", "pid": 0}),
        encoding="utf-8",
    )
    (peer / "current-version").write_text(version, encoding="utf-8")
    return python


def test_completed_installed_owner_precedes_path(installation):
    _, peer, which = installation
    complete_slot(peer)
    assert lifecycle._agent_worktrees_launch_prefix() == [
        sys.executable, "-I", "-X", "utf8", "-m", "agent_bridge.worktree_owner_launch",
        str(installation[0]),
    ]
    which.assert_not_called()


@pytest.mark.parametrize("state", ["absent", "incomplete", "corrupt"])
def test_absent_or_uncompleted_runtime_does_not_fabricate_owner(installation, state):
    _, peer, which = installation
    if state != "absent":
        python = complete_slot(peer)
        marker = python.parent.parent / ".install-complete.json"
        marker.write_text("" if state == "incomplete" else "{}", encoding="utf-8")
    assert lifecycle._agent_worktrees_launch_prefix() is None
    which.assert_called_once_with("agent-worktrees")


def test_retired_legacy_owner_never_uses_global_command(installation):
    _, peer, which = installation
    complete_slot(peer)
    (peer / ".installation-ownership.json").write_text("{}", encoding="utf-8")
    with pytest.raises(ContextRefused, match="installation-cell ownership"):
        lifecycle._agent_worktrees_launch_prefix()
    which.assert_not_called()


@pytest.mark.parametrize("explicit", [False, True])
def test_cell_service_uses_validated_own_context_not_path(
    installation, monkeypatch, explicit,
):
    own, _, which = installation
    cell_own = own.parent / "marketplaces" / "test-cell" / "plugins" / "agent-bridge"
    peer = cell_own.parent / "agent-worktrees"
    peer.mkdir(parents=True)
    context = str(cell_own / "install.json")
    if explicit:
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context)
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: cell_own)
    validated = Mock(return_value={
        "cellRoot": str(cell_own.parent.parent), "pluginRoot": str(cell_own),
    })
    launch = Mock(return_value=["validated-peer"])
    monkeypatch.setattr(lifecycle._peer_launch, "validate_owner", validated)
    monkeypatch.setattr(lifecycle._peer_launch, "launch_prefix", launch)
    assert lifecycle._agent_worktrees_launch_prefix() == ["validated-peer"]
    validated.assert_called_once_with("agent-bridge", cell_own, context)
    launch.assert_called_once_with("agent-bridge", cell_own, context, "agent-worktrees")
    which.assert_not_called()


def test_explicit_context_refusal_remains_typed(installation, monkeypatch):
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", "/invalid/install.json")
    monkeypatch.setattr(
        lifecycle._peer_launch, "validate_owner", Mock(side_effect=ValueError("invalid owner")),
    )
    with pytest.raises(ContextRefused, match="invalid owner"):
        lifecycle._agent_worktrees_launch_prefix()
    installation[2].assert_not_called()


def test_missing_cell_peer_never_captures_legacy_runtime(installation, monkeypatch):
    own, peer, which = installation
    complete_slot(peer)
    cell_own = own.parent / "marketplaces" / "test-cell" / "plugins" / "agent-bridge"
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: cell_own)
    monkeypatch.setattr(lifecycle._peer_launch, "validate_owner", Mock(return_value={
        "cellRoot": str(cell_own.parent.parent), "pluginRoot": str(cell_own),
    }))
    assert lifecycle._agent_worktrees_launch_prefix() is None
    which.assert_not_called()


def test_guidance_renderer_retains_service_derived_cell(installation, monkeypatch):
    own, _, _ = installation
    cell_own = own.parent / "marketplaces" / "test-cell" / "plugins" / "agent-bridge"
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: cell_own)
    monkeypatch.setattr(lifecycle, "_agent_worktrees_launch_prefix", lambda: ["peer", "-I"])
    monkeypatch.setattr(
        local_cache_refresh, "_resolve_same_cell_agent_worktrees_path", lambda: "/cell/command",
    )
    assert local_cache_refresh._resolve_agent_worktrees_path() == ("/cell/command", False)
    monkeypatch.setattr(
        lifecycle, "_agent_worktrees_launch_prefix", Mock(side_effect=ContextRefused("refused")),
    )
    assert local_cache_refresh._resolve_agent_worktrees_path() == (None, True)


@pytest.mark.asyncio
async def test_actual_installed_peer_executes_with_service_path(installation, monkeypatch):
    _, peer, which = installation
    python = complete_slot(peer, interpreter=False)
    slot = python.parent.parent
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(slot)],
        check=True, capture_output=True, timeout=20, **no_window_kwargs(),
    )
    query = subprocess.run(
        [str(python), "-I", "-c",
         "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True, capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    package = Path(query.stdout.strip()) / "agent_worktrees"
    package.mkdir()
    (package / "__main__.py").write_text(
        "import json, os, sys\n"
        "assert 'AGENT_RT_ROOT' not in os.environ\n"
        "assert 'COPILOT_PLUGIN_ROOT' not in os.environ\n"
        "assert 'AGENT_HOME' not in os.environ\n"
        "print(json.dumps({'argv': sys.argv[1:]}))\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_RT_ROOT", str(installation[0]))
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", "/caller/payload")
    monkeypatch.setenv("AGENT_HOME", "/caller/registry")
    result = await authority.worktree_command(["list", "--json", "--fresh"], "project-a")
    assert result == {"argv": ["--project", "project-a", "list", "--json", "--fresh"]}
    which.assert_not_called()


@pytest.mark.asyncio
async def test_installed_owner_verifies_exact_record_and_independent_head(installation, monkeypatch):
    _, peer, which = installation
    python = complete_slot(peer, interpreter=False)
    slot = python.parent.parent
    subprocess.run(
        [sys.executable, "-m", "venv", "--without-pip", str(slot)],
        check=True, capture_output=True, timeout=20, **no_window_kwargs(),
    )
    query = subprocess.run(
        [str(python), "-I", "-c",
         "import sysconfig; print(sysconfig.get_path('purelib'))"],
        check=True, capture_output=True, text=True, timeout=10, **no_window_kwargs(),
    )
    package = Path(query.stdout.strip()) / "agent_worktrees"
    package.mkdir()
    (package / "__main__.py").write_text(
        "import json, sys\n"
        "assert sys.argv[1:3] == ['--project', 'project-a']\n"
        "args = sys.argv[3:]\n"
        "if args[0] == 'list':\n"
        " assert args == ['list', '--json', '--cache-only', '--worktree-id', 'worktree-a']\n"
        " result = {'worktrees': [{'id': 'worktree-a', 'path': '/fixture', "
        "'status': 'active', 'state': 'unknown'}]}\n"
        "else:\n"
        " assert args == ['head-session', '--worktree-id', 'worktree-a', '--json']\n"
        " result = {'tracked': True, 'head_session': 'native-a', 'active': True, 'state': 'active'}\n"
        "print(json.dumps(result))\n",
        encoding="utf-8",
    )
    request = AcquisitionRequest.model_validate({
        "target": "task-worker", "mode": "cli", "task_id": "task-a",
        "task_generation": 0, "worktree_id": "worktree-a", "allocation": "created",
        "conversation_retired": False, "reservation_key": "reservation-a",
        "delivery_id": "delivery-a", "prompt": "Perform the assigned task",
        "project": "project-a",
    })
    row, head = await authority.verify_worktree(request)
    assert row["id"] == "worktree-a"
    assert head == "native-a"
    which.assert_not_called()


def test_handoff_check_scrubs_owner_routing(installation, monkeypatch, capsys):
    complete_slot(installation[1])
    for key in (
        "AGENT_HOME", "AGENT_RT_ROOT", "AGENT_RT_PY", "COPILOT_PLUGIN_ROOT",
        "VIRTUAL_ENV", "PYTHONHOME", "PYTHONPATH", "__PYVENV_LAUNCHER__",
    ):
        monkeypatch.setenv(key, "/caller/override")
    run = Mock(return_value=subprocess.CompletedProcess([], 0, '{"findings":[]}', ""))
    monkeypatch.setattr(lifecycle.subprocess, "run", run)
    with pytest.raises(SystemExit) as error:
        lifecycle._cmd_handoff_check(argparse.Namespace(
            worktree_id="worktree-a", execute=True, json=True,
        ))
    assert error.value.code == 0
    assert run.call_args.args[0][:6] == [
        sys.executable, "-I", "-X", "utf8", "-m", "agent_bridge.worktree_owner_launch",
    ]
    env = run.call_args.kwargs["env"]
    assert "AGENT_HOME" not in env
    assert not any(key in env for key in (
        "PYTHONHOME", "PYTHONPATH", "COPILOT_PLUGIN_ROOT", "AGENT_RT_ROOT",
        "AGENT_RT_PY", "VIRTUAL_ENV", "__PYVENV_LAUNCHER__",
    ))
    assert json.loads(capsys.readouterr().out) == {"findings": []}


def test_legacy_trampoline_rechecks_retirement_before_execution(installation):
    complete_slot(installation[1])
    prefix = lifecycle._agent_worktrees_launch_prefix()
    assert prefix
    (installation[1] / ".installation-ownership.json").write_text("{}", encoding="utf-8")
    result = subprocess.run(
        [*prefix, "list", "--json"], env=lifecycle._agent_worktrees_environment(),
        capture_output=True, text=True, timeout=15, **no_window_kwargs(),
    )
    assert result.returncode == 126
    assert "installation-cell ownership" in result.stderr
    assert not result.stdout


@pytest.mark.asyncio
async def test_context_failure_is_owner_capability_refusal(monkeypatch):
    monkeypatch.setattr(
        authority, "_agent_worktrees_launch_prefix",
        Mock(side_effect=ContextRefused("cell refused")),
    )
    with pytest.raises(AcquisitionRefused) as error:
        await authority.worktree_command(["list", "--json"], None)
    assert error.value.code == "worktrees_unavailable"
    assert error.value.effect is None
