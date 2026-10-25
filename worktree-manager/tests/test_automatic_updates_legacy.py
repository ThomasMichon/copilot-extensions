"""Read-only owner handover never substitutes an unknown inventory for absence."""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

import pytest

from worktree_manager import agent_plugin_runtime
from worktree_manager import automatic_updates_legacy as legacy
from worktree_manager.automatic_updates_policy import PolicyError


def _inventory() -> dict:
    return {
        "schema": "copilot-extensions.legacy-update-inventory", "version": 1,
        "ok": True, "adoption_ready": True,
        "legacy_artifacts": [
            {
                "kind": kind, "present": False, "in_flight": False,
                "retirement_required": False, "blocks_adoption": False,
                "inspection_complete": True, "detail": "absent",
            }
            for kind in ("fleet", "self")
        ],
    }


@pytest.fixture
def installed(monkeypatch, tmp_path: Path):
    monkeypatch.setattr(
        agent_plugin_runtime, "resolve_installed_plugin_command",
        lambda plugin: ["installed-python", "-m", "agent_machines"],
    )
    monkeypatch.setattr(agent_plugin_runtime, "legacy_plugin_root", lambda plugin: tmp_path / plugin)
    monkeypatch.setattr(legacy, "default_root", lambda: tmp_path, raising=False)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    return tmp_path


def test_public_inventory_uses_exact_installed_command(installed, monkeypatch) -> None:
    calls = []

    def run(argv, **kwargs):
        calls.append((argv, kwargs))
        return SimpleNamespace(returncode=0, stdout=json.dumps(_inventory()), stderr="")

    monkeypatch.setattr(legacy.subprocess, "run", run)
    assert legacy.installed_inventory()["adoption_ready"]
    assert calls[0][0] == [
        "installed-python", "-m", "agent_machines", "fleet-update", "inventory", "--json",
    ]
    assert calls[0][1]["env"]["GIT_TERMINAL_PROMPT"] == "0"


@pytest.mark.parametrize("change", [
    {"version": True}, {"version": 2}, {"ok": False}, {"adoption_ready": False},
    {"legacy_artifacts": []}, {"legacy_artifacts": [{"kind": []}, {"kind": "self"}]},
])
def test_incomplete_public_inventory_blocks_adoption(installed, monkeypatch, change) -> None:
    monkeypatch.setattr(
        legacy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps({**_inventory(), **change}), stderr="",
        ),
    )
    with pytest.raises(PolicyError):
        legacy.require_legacy_retired()


@pytest.mark.parametrize("kind,key,value", [
    ("fleet", "present", True), ("fleet", "retirement_required", True),
    ("self", "in_flight", True), ("self", "in_flight", None),
    ("self", "blocks_adoption", True),
])
def test_ready_claim_cannot_override_artifact_evidence(
    installed, monkeypatch, kind: str, key: str, value: object,
) -> None:
    payload = _inventory()
    next(item for item in payload["legacy_artifacts"] if item["kind"] == kind)[key] = value
    monkeypatch.setattr(
        legacy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
            returncode=0, stdout=json.dumps(payload), stderr="",
        ),
    )
    with pytest.raises(PolicyError, match="handover is blocked"):
        legacy.require_legacy_retired()


def test_older_installed_cli_is_unavailable_not_absent(installed, monkeypatch) -> None:
    monkeypatch.setattr(
        legacy.subprocess, "run", lambda *args, **kwargs: SimpleNamespace(
            returncode=2, stdout="", stderr="invalid choice: inventory",
        ),
    )
    with pytest.raises(PolicyError, match="canonical installer"):
        legacy.require_legacy_retired()


def test_corrupt_install_receipt_is_not_optional_absence(tmp_path: Path, monkeypatch) -> None:
    root = tmp_path / "agent-machines"
    root.mkdir()
    (root / "current-version").write_text("1.0.0", encoding="utf-8")
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_command", lambda plugin: None)
    monkeypatch.setattr(agent_plugin_runtime, "legacy_plugin_root", lambda plugin: root)
    with pytest.raises(PolicyError, match="inventory runtime is unavailable"):
        legacy.require_legacy_retired()


def test_absent_optional_plugin_still_checks_native_triggers(tmp_path: Path, monkeypatch) -> None:
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_command", lambda plugin: None)
    monkeypatch.setattr(agent_plugin_runtime, "legacy_plugin_root", lambda plugin: tmp_path / plugin)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    monkeypatch.setattr(legacy, "legacy_triggers", lambda: ["owned legacy timer"])
    with pytest.raises(PolicyError, match="retirement"):
        legacy.require_legacy_retired()


def test_orphaned_manual_lock_is_not_ignored(tmp_path: Path, monkeypatch) -> None:
    lock = tmp_path / ".agent-machines" / "self-update" / "sweep.lock.json"
    lock.parent.mkdir(parents=True)
    lock.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(agent_plugin_runtime, "resolve_installed_plugin_command", lambda plugin: None)
    monkeypatch.setattr(agent_plugin_runtime, "legacy_plugin_root", lambda plugin: tmp_path / plugin)
    monkeypatch.setenv("HOME", str(tmp_path))
    monkeypatch.setenv("USERPROFILE", str(tmp_path))
    with pytest.raises(PolicyError, match="requires verification"):
        legacy.require_legacy_retired()
