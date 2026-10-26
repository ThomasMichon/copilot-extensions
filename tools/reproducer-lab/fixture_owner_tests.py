"""Explicit local Git/config-owner controls, not actual guest qualification."""

from __future__ import annotations

import asyncio
import importlib
import json
import os
import sys
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

LAB = Path(__file__).resolve().parent
REPO = LAB.parents[1]
sys.path.insert(0, str(LAB))
import guest_fixture

pytestmark = pytest.mark.skipif(sys.platform != "linux", reason="explicit local Linux fixture lane")


@pytest.fixture
def prepared(tmp_path: Path, monkeypatch: Any) -> SimpleNamespace:
    state, workspace, evidence = [tmp_path / name for name in ("state", "workspace", "evidence")]
    for path in (state, workspace, evidence):
        path.mkdir(mode=0o700)
    (state / guest_fixture.LOCK.name).touch()
    (state / "runtime").mkdir(mode=0o700)
    for name, value in {
        "STATE": state, "WORKSPACE": workspace, "EVIDENCE": evidence,
        "HOME": state / "home", "SOURCE": REPO,
    }.items():
        monkeypatch.setattr(guest_fixture, name, value)
    # Host fixtures do not qualify guest UID 1000, mounts, runtime or source.
    monkeypatch.setattr(guest_fixture, "_private_directory", lambda *_a, **_kw: None)
    custody = SimpleNamespace(assert_owned=lambda: None)
    guest_fixture.prepare_fixture(custody)
    for key in tuple(os.environ):
        if key.startswith(("AGENT_", "COPILOT_", "GIT_")):
            monkeypatch.delenv(key)
    for key, value in guest_fixture.environment().items():
        monkeypatch.setenv(key, value)
    for path in (REPO / "libs").glob("*/src"):
        monkeypatch.syspath_prepend(str(path))
    for plugin in ("agent-worktrees", "agent-bridge", "agent-dispatch"):
        monkeypatch.syspath_prepend(str(REPO / "plugins" / plugin / "src"))
    return SimpleNamespace(state=state, workspace=workspace, custody=custody)


def test_local_git_created_only_synthetic_harness(prepared: SimpleNamespace) -> None:
    git = prepared.workspace / "anchor/.git"
    assert git.is_dir()
    assert (git / "HEAD").read_text().strip() == "ref: refs/heads/main"
    assert "remote " not in (git / "config").read_text()
    with pytest.raises(ValueError, match="original owner"):
        guest_fixture.prepare_fixture(prepared.custody)


def test_actual_peer_launcher_accepts_protocol_flags_before_extra_args(
    prepared: SimpleNamespace,
) -> None:
    from process import run

    launcher = guest_fixture.HOME / "bin/mock-acp"
    assert launcher.stat().st_mode & 0o777 == 0o700
    transport = importlib.import_module("agent_bridge.transport")
    target = transport.SpawnTarget(
        type="local", cwd=str(prepared.workspace / "anchor"),
        project=guest_fixture.PROJECT, explicit_cwd=True,
        copilot_path=str(launcher), env=guest_fixture.environment(),
    )
    arguments, cwd, _environment = asyncio.run(transport.resolve_local_launch(target))
    assert arguments == [str(launcher), "--acp", "--stdio", "--no-auto-update"]
    assert cwd == str(prepared.workspace / "anchor")
    result = run(
        [*arguments, "--help"],
        timeout=5, max_stdout_bytes=16 * 1024, env=guest_fixture.environment(),
    )
    assert result.returncode == 0, result.stderr
    assert "--acp" in result.stdout and "--stdio" in result.stdout
    assert not (guest_fixture.EVIDENCE / "acp-peer.jsonl").exists()


def test_actual_owner_loaders_select_fixture_inputs(prepared: SimpleNamespace) -> None:
    worktrees = importlib.import_module("agent_worktrees.config")
    worktrees.set_active_project(guest_fixture.PROJECT)
    config = worktrees.load_config()
    assert Path(config.default_repo.anchor) == prepared.workspace / "anchor"
    assert Path(config.default_repo.worktree_root) == prepared.workspace / "worktrees"
    policy = importlib.import_module("repository_policy")
    assert policy.require_agent_hosting(prepared.workspace / "anchor").expose_agent is True
    assert config.auto_fast_forward is False
    bridge = importlib.import_module("agent_bridge.config").load_config()
    assert Path(bridge.db_path) == prepared.state / "bridge/bridge.db"
    assert bridge.bind == "127.0.0.1" and bridge.port == 0
    assert bridge.enable_credential_relay is False and bridge.idle_reap_ttl_seconds == 0
    assert importlib.import_module("agent_bridge.install_paths").uses_legacy_install_dir()
    registry = importlib.import_module("agent_bridge.agent_registry_topology")
    agents = registry.load_agent_registry(
        guest_fixture.HOME / ".agent-bridge/acp-agents.json", strict=True,
    )
    assert set(agents) == {guest_fixture.PROJECT}
    assert agents[guest_fixture.PROJECT].project == guest_fixture.PROJECT
    dispatch = importlib.import_module("agent_dispatch.config").load_config()
    assert Path(dispatch.db_path) == prepared.state / "dispatch/queue.db"
    assert dispatch.host == "127.0.0.1" and dispatch.port == 0
    assert dispatch.handoff_fallback_enabled is False and dispatch.sweep_interval == 0
    assert json.loads((prepared.workspace / "anchor/.github/copilot/settings.json").read_text()) == {
        "enabledPlugins": {}, "extraKnownMarketplaces": {},
    }
