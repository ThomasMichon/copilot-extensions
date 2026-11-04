"""Optional Bridge attribution selects data, never a service-start or import fallback."""

from __future__ import annotations

import json
import os
from pathlib import Path
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


@pytest.mark.parametrize("declared_environment", [None, "OTHER_NO_SELFPROVISION"])
def test_old_payload_without_owning_no_provision_boundary_is_not_invoked(
    tmp_path, monkeypatch, declared_environment,
) -> None:
    payload = tmp_path / "payload"
    (payload / "bin").mkdir(parents=True)
    suffix = "agent-bridge.ps1" if os.name == "nt" else "agent-bridge"
    (payload / "bin" / suffix).write_text("must not execute")
    (payload / "payload-invocation.json").write_text(json.dumps({
        "schema": "copilot-extensions.payload-invocation", "command": "agent-bridge",
        "module": "agent_bridge", "noSelfProvisionEnv": declared_environment,
    }))
    selected = SimpleNamespace(root_for_scope=lambda scope: payload)
    monkeypatch.setattr(bridge, "resolve_active_plugins", lambda **kwargs: SimpleNamespace(
        authority=SimpleNamespace(value="complete"), decisions=[selected], active={"selected": selected},
    ))
    with pytest.raises(OwnerReadError, match="non-provisioning"):
        bridge.bridge_command(str(tmp_path))
