"""Manager bootstrap must not admit or publish the Worktrees installation."""
from __future__ import annotations

import json
import os
from pathlib import Path
import runpy

import pytest


@pytest.fixture
def admission(tmp_path, monkeypatch):
    home = tmp_path / "home"
    commands = home / ".local/bin"
    commands.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    monkeypatch.delenv("AGENT_WORKTREES_CONTROL_PLANE_PROVIDER", raising=False)
    monkeypatch.setenv(
        "AGENT_WORKTREES_CONTROL_PLANE_PROVIDERS_DIR",
        str(home / ".agent-worktrees/control-plane-providers.d"),
    )
    repo = Path(__file__).resolve().parents[2]
    api = runpy.run_path(str(repo / "worktree-manager/scripts/update_entry.py"))
    return api, home, commands, repo / "worktree-manager"


def test_bootstrap_ignores_old_helper_after_registered_payload_refresh(admission, monkeypatch):
    api, home, commands, payload = admission
    root = home / ".agent-worktrees"
    root.mkdir()
    registered = home / ".copilot/installed-plugins/example/agent-worktrees"
    (registered / "bin").mkdir(parents=True)
    helper = "agent-worktrees-update-entry.ps1"
    (registered / "bin" / helper).write_bytes(b"new payload helper\n")
    (commands / helper).write_bytes(b"old installed helper\r\n")
    manifest = root / "deploy-manifest.json"
    original = json.dumps({
        "schema_version": 3, "service": "agent-worktrees",
        "source": {"kind": "marketplace", "path": str(registered), "version": "1.0.0"},
    }).encode()
    manifest.write_bytes(original)
    (root / "current-version").write_text("broken-runtime")

    def engine_admission_forbidden(*args, **kwargs):
        pytest.fail("Manager bootstrap must not resolve Worktrees publication ownership")

    monkeypatch.setitem(
        api["validate_manager_ownership"].__globals__,
        "_publication_api", engine_admission_forbidden,
    )
    api["validate_manager_ownership"](home / ".worktree-manager", payload)
    assert manifest.read_bytes() == original
    assert (commands / helper).read_bytes() == b"old installed helper\r\n"
    assert not (home / ".worktree-manager").exists()


@pytest.mark.parametrize("name", [
    "worktree-manager", "worktree-manager.cmd", "worktree-manager.ps1",
])
def test_bootstrap_preserves_foreign_manager_command(admission, name):
    api, home, commands, payload = admission
    path = commands / name
    path.write_bytes(b"unrelated command\n")
    with pytest.raises(RuntimeError, match="Foreign Manager command preserved"):
        api["validate_manager_ownership"](home / ".worktree-manager", payload)
    assert path.read_bytes() == b"unrelated command\n"


@pytest.mark.parametrize("fault", ["alternate", "wrong-root", "wrong-command"])
def test_bootstrap_preserves_foreign_manager_registration(admission, fault):
    api, home, commands, payload = admission
    registry = home / ".agent-worktrees/control-plane-providers.d"
    registry.mkdir(parents=True)
    root = home / ".worktree-manager"
    primary = "worktree-manager.cmd" if os.name == "nt" else "worktree-manager"
    document = {
        "schema_version": 1, "provider": "worktree-manager",
        "provider_root": str(root), "command": [str(commands / primary)],
    }
    if fault == "alternate":
        document["provider"] = "another-provider"
    elif fault == "wrong-root":
        document["provider_root"] = str(home / "another-manager")
    else:
        document["command"] = [str(commands / "another-manager")]
    path = registry / "provider.json"
    original = json.dumps(document).encode()
    path.write_bytes(original)
    with pytest.raises(RuntimeError):
        api["validate_manager_ownership"](root, payload)
    assert path.read_bytes() == original


def test_bootstrap_preserves_foreign_manager_package(admission):
    api, home, _commands, payload = admission
    root = home / ".worktree-manager"
    slot = root / "versions/1.0.0"
    slot.mkdir(parents=True)
    (root / "current-version").write_text("1.0.0")
    (slot / "pyproject.toml").write_text('[project]\nname = "unrelated-package"\n')
    with pytest.raises(RuntimeError, match="Manager slot belongs to another package"):
        api["validate_manager_ownership"](root, payload)


@pytest.mark.parametrize("name", [
    "worktree-manager", "worktree-manager.cmd", "worktree-manager.ps1",
])
def test_registered_manager_can_repair_changed_launcher_bytes(admission, name):
    api, home, commands, payload = admission
    root = home / ".worktree-manager"
    registry = home / ".agent-worktrees/control-plane-providers.d"
    registry.mkdir(parents=True)
    primary = "worktree-manager.cmd" if os.name == "nt" else "worktree-manager"
    (registry / "worktree-manager.json").write_text(json.dumps({
        "schema_version": 1, "provider": "worktree-manager",
        "provider_root": str(root), "command": [str(commands / primary)],
    }))
    path = commands / name
    original = b"older, newer or damaged registered Manager launcher\n"
    path.write_bytes(original)
    before = api["validate_manager_ownership"](root, payload)
    assert path.read_bytes() == original
    path.write_bytes(b"changed concurrently\n")
    with pytest.raises(RuntimeError, match="changed during construction"):
        api["validate_manager_ownership"](root, payload, expected=before)
    assert path.read_bytes() == b"changed concurrently\n"
