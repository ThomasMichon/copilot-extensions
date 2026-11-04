"""Optional Bridge attribution selects data, never a service-start or import fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
from threading import Event
from types import SimpleNamespace

import pytest

from worktree_manager import companion_bridge_source as bridge
from worktree_manager.companion_worktree_codec import OwnerReadError
from test_agenda_end_to_end import ROOT


def test_active_project_payload_is_selected_without_runtime_import_or_start(tmp_path, monkeypatch) -> None:
    home, checkout = tmp_path / "profile", tmp_path / "checkout"
    home.mkdir()
    checkout.mkdir()
    for name in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(name, str(home))
    for name in ("COPILOT_EXTENSIONS_CONTEXT", "COPILOT_PLUGIN_ROOT", "COPILOT_HOME"):
        monkeypatch.delenv(name, raising=False)
    settings = checkout / ".github/copilot/settings.json"
    settings.parent.mkdir(parents=True)
    body = {
        "enabledPlugins": {"agent-bridge@source-fixture": True},
        "extraKnownMarketplaces": {"source-fixture": {
            "source": {"source": "directory", "path": str(ROOT), "manifestName": "copilot-extensions"},
        }},
    }
    settings.write_text(json.dumps(body))
    command = bridge.bridge_command(str(checkout))
    suffix = "agent-bridge.ps1" if os.name == "nt" else "agent-bridge"
    assert Path(command[-1]) == ROOT / "plugins/agent-bridge/bin" / suffix
    body["enabledPlugins"]["agent-bridge@source-fixture"] = False
    settings.write_text(json.dumps(body))
    with pytest.raises(OwnerReadError, match="unsupported"):
        bridge.bridge_command(str(checkout))


@pytest.mark.parametrize("declaration", [
    None, [], {
        "schema": "copilot-extensions.payload-invocation", "command": "agent-bridge",
        "module": "agent_bridge", "noSelfProvisionEnv": None,
    }, {
        "schema": "copilot-extensions.payload-invocation", "command": "agent-bridge",
        "module": "agent_bridge", "noSelfProvisionEnv": "OTHER_NO_SELFPROVISION",
    },
])
def test_old_payload_without_owning_no_provision_boundary_is_not_invoked(
    tmp_path, monkeypatch, declaration,
) -> None:
    payload = tmp_path / "payload"
    (payload / "bin").mkdir(parents=True)
    suffix = "agent-bridge.ps1" if os.name == "nt" else "agent-bridge"
    (payload / "bin" / suffix).write_text("must not execute")
    (payload / "payload-invocation.json").write_text(json.dumps(declaration))
    selected = SimpleNamespace(root_for_scope=lambda scope: payload)
    monkeypatch.setattr(bridge, "resolve_active_plugins", lambda **kwargs: SimpleNamespace(
        authority=SimpleNamespace(value="complete"), decisions=[selected], active={"selected": selected},
    ))
    with pytest.raises(OwnerReadError, match="non-provisioning"):
        bridge.bridge_command(str(tmp_path))


@pytest.mark.portfolio_tier("T3")
@pytest.mark.effect("filesystem")
@pytest.mark.skipif(os.name == "nt", reason="POSIX payload gate; native Windows remains separate")
def test_real_bridge_payload_gate_does_not_inherit_the_worktrees_receipt(tmp_path, monkeypatch) -> None:
    home, checkout = tmp_path / "home", tmp_path / "checkout"
    home.mkdir()
    checkout.mkdir()
    for key in ("HOME", "USERPROFILE", "AGENT_HOME"):
        monkeypatch.setenv(key, str(home))
    for key in tuple(os.environ):
        if key.startswith(("AGENT_BRIDGE_", "AGENT_RT_", "COPILOT_EXTENSIONS_")):
            monkeypatch.delenv(key)
    monkeypatch.setenv("COPILOT_HOME", str(home / ".copilot"))
    foreign_root = str(ROOT / "plugins/agent-worktrees")
    monkeypatch.setenv("COPILOT_PLUGIN_ROOT", foreign_root)
    foreign = str(home / "agent-worktrees-receipt.json")
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", foreign)
    settings = checkout / ".github/copilot/settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "enabledPlugins": {"agent-bridge@source-fixture": True},
        "extraKnownMarketplaces": {"source-fixture": {
            "source": {"source": "directory", "path": str(ROOT), "manifestName": "copilot-extensions"},
        }},
    }))
    commands = bridge.MembershipCommands("fixture-host/example/selected", cwd=str(checkout), project_root=str(checkout))
    with pytest.raises(OwnerReadError, match="passive observation; no self-provision"):
        list(commands.frames(Event()))
    assert os.environ["COPILOT_EXTENSIONS_CONTEXT"] == foreign
    assert os.environ["COPILOT_PLUGIN_ROOT"] == foreign_root
    assert not (home / ".agent-bridge").exists()
