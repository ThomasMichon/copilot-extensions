"""Explicit reservation repair retains pending status and all unrelated state."""
from __future__ import annotations

import importlib.util
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys

import pytest

from agent_worktrees import _command_binding as binding, _installation_context as primitive
from test_installer_command_binding import owned_install  # noqa: F401

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "reservation_recovery", ROOT / "scripts" / "recover_command_lifecycle.py",
)
assert spec and spec.loader
recovery = importlib.util.module_from_spec(spec)
previous_binding = sys.modules.get("command_binding")
sys.modules["command_binding"] = binding
try:
    spec.loader.exec_module(recovery)
finally:
    if previous_binding is None:
        del sys.modules["command_binding"]
    else:
        sys.modules["command_binding"] = previous_binding


@pytest.fixture
def interrupted(request, monkeypatch):
    home, runtime = request.getfixturevalue("owned_install")
    payload = home / ".copilot/installed-plugins/example/agent-worktrees"
    arguments = dict(
        payload_root=payload, plugin_id="agent-worktrees", legacy_root=runtime,
        project_root=home, environment={**os.environ, "COPILOT_PLUGIN_ROOT": str(payload)},
    )
    monkeypatch.delenv("COPILOT_EXTENSIONS_COMMAND_LIFECYCLE", raising=False)
    settings = home / ".copilot/settings.json"
    declared = settings.read_bytes()
    settings.write_text("{}", encoding="utf-8")
    lifecycle = binding._load_lifecycle()
    with pytest.raises(RuntimeError, match="interrupted"):
        with lifecycle.legacy_lifecycle(binding._rules(), primitive, **arguments):
            raise RuntimeError("interrupted")
    settings.write_bytes(declared)
    path = runtime / "deploy-manifest.json"
    manifest = json.loads(path.read_text())
    manifest["retained"] = {"configuration": [1, 2], "source": {"version": "0.1.0"}}
    path.write_text(json.dumps(manifest), encoding="utf-8")
    (runtime / "existing-runtime").write_bytes(b"retained runtime")
    return arguments, path


def test_inspection_is_read_only_and_normal_update_still_refuses(interrupted):
    arguments, path = interrupted
    before = path.read_bytes()
    plan = recovery.recover(primitive, **arguments)
    assert plan["status"] == "repairable"
    assert plan["commandReady"] is False
    assert plan["ordinaryUpdateRequired"] is True
    assert path.read_bytes() == before
    lock = binding._load_lifecycle()._lock(primitive, arguments)
    assert not lock.path.exists()
    with pytest.raises(binding.CommandOwnershipConflict, match="another owner"):
        binding.publish_legacy_binding(primitive, **arguments)


def test_repair_preserves_state_and_requires_real_publication(interrupted):
    arguments, path = interrupted
    before = json.loads(path.read_text())
    plan = recovery.recover(primitive, **arguments)
    result = recovery.recover(primitive, expected_evidence=plan["expectedEvidence"], **arguments)
    assert result["status"] == "repaired"
    after = json.loads(path.read_text())
    assert {key: value for key, value in after.items() if key != "commandLifecycle"} == {
        key: value for key, value in before.items() if key != "commandLifecycle"
    }
    assert "commandBinding" not in after
    assert after["commandLifecycle"]["state"] == "pending"
    assert (path.parent / "existing-runtime").read_bytes() == b"retained runtime"
    assert binding.resolve_command_binding(primitive, **arguments)["reason"] == "legacy-lifecycle-incomplete"
    with binding._load_lifecycle().legacy_lifecycle(binding._rules(), primitive, **arguments):
        binding.write_legacy_manifest(primitive, after, **arguments)
    assert binding.resolve_command_binding(primitive, **arguments)["status"] == "selected"


@pytest.mark.parametrize("change", ["payload", "runtime", "environment", "extra", "complete", "binding", "boolean", "unattributed"])
def test_foreign_or_invalid_owner_is_preserved(interrupted, change):
    arguments, path = interrupted
    manifest = json.loads(path.read_text())
    owner = manifest["commandLifecycle"]["owner"]
    if change == "payload":
        owner["payloadRoot"] = str(path.parent / "other-payload")
    elif change == "runtime":
        owner["runtimeRoot"] = str(path.parent / "other-runtime")
    elif change == "environment":
        owner["environment"]["homeRealPath"] = str(path.parent / "other-home")
    elif change == "extra":
        owner["unexpected"] = True
    elif change == "complete":
        manifest["commandLifecycle"]["state"] = "complete"
    elif change == "binding":
        manifest["commandBinding"] = {}
    elif change == "unattributed":
        owner["unattributed"] = 1
    else:
        manifest["commandLifecycle"]["version"] = True
    path.write_text(json.dumps(manifest), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(binding.CommandOwnershipConflict):
        recovery.recover(primitive, **arguments)
    assert path.read_bytes() == before
    assert not binding._load_lifecycle()._lock(primitive, arguments).path.exists()


def test_changed_evidence_refuses_without_mutation(interrupted):
    arguments, path = interrupted
    plan = recovery.recover(primitive, **arguments)
    path.write_bytes(path.read_bytes() + b"\n")
    before = path.read_bytes()
    with pytest.raises(binding.CommandOwnershipConflict, match="evidence changed"):
        recovery.recover(primitive, expected_evidence=plan["expectedEvidence"], **arguments)
    assert path.read_bytes() == before


def test_source_change_after_inspection_invalidates_evidence(interrupted):
    arguments, path = interrupted
    plan = recovery.recover(primitive, **arguments)
    settings = arguments["project_root"] / ".copilot/settings.json"
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Other/Plugins"}}
        },
    }), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(binding.CommandOwnershipConflict, match="evidence changed"):
        recovery.recover(primitive, expected_evidence=plan["expectedEvidence"], **arguments)
    assert path.read_bytes() == before


def test_enabled_installation_policy_refuses_legacy_recovery(interrupted):
    arguments, path = interrupted
    policy = arguments["project_root"] / ".copilot-extensions/installation-mode.json"
    policy.parent.mkdir(exist_ok=True)
    policy.write_text(json.dumps({
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    }), encoding="utf-8")
    before = path.read_bytes()
    with pytest.raises(binding.CommandOwnershipConflict, match="policy"):
        recovery.recover(primitive, **arguments)
    assert path.read_bytes() == before


@pytest.mark.parametrize("repair", [False, True])
def test_inherited_marketplace_mismatch_preserves_reservation(interrupted, monkeypatch, repair):
    arguments, path = interrupted
    plan = recovery.recover(primitive, **arguments)
    resolve_mode = primitive.resolve_installation_mode

    def other_marketplace(**kwargs):
        return {**resolve_mode(**kwargs), "marketplaceId": "other--0123456789abcdef"}

    monkeypatch.setattr(primitive, "resolve_installation_mode", other_marketplace)
    before = path.read_bytes()
    with pytest.raises(binding.CommandOwnershipConflict, match="marketplace.*provenance"):
        recovery.recover(
            primitive, **arguments,
            expected_evidence=plan["expectedEvidence"] if repair else None,
        )
    assert path.read_bytes() == before
    assert not binding._load_lifecycle()._lock(primitive, arguments).path.exists()


def test_live_publication_lock_is_not_borrowed_or_removed(interrupted):
    arguments, path = interrupted
    plan = recovery.recover(primitive, **arguments)
    before = path.read_bytes()
    lock = binding._load_lifecycle()._lock(primitive, arguments)
    with lock:
        owner = lock.owner_path.read_bytes()
        with pytest.raises(binding.CommandOwnershipConflict, match="lock exists"):
            recovery.recover(primitive, **arguments)
        with pytest.raises(primitive.InstallationContextError, match="busy|held"):
            recovery.recover(primitive, expected_evidence=plan["expectedEvidence"], **arguments)
        assert lock.owner_path.read_bytes() == owner
        lock.assert_owned()
    assert path.read_bytes() == before


def test_source_and_policy_are_revalidated_under_lock(interrupted, monkeypatch):
    arguments, path = interrupted
    plan = recovery.recover(primitive, **arguments)
    before = path.read_bytes()
    real_lock = recovery.binding._load_lifecycle()._lock

    def changed(*args, **kwargs):
        (arguments["project_root"] / ".copilot/settings.json").write_text("{}", encoding="utf-8")
        return real_lock(*args, **kwargs)

    monkeypatch.setattr(recovery.binding._load_lifecycle(), "_lock", changed)
    with pytest.raises(primitive.InstallationContextError):
        recovery.recover(primitive, expected_evidence=plan["expectedEvidence"], **arguments)
    assert path.read_bytes() == before


def test_helper_help_does_not_write_payload_bytecode(tmp_path):
    scripts = tmp_path / "scripts"
    scripts.mkdir()
    for name in ("recover_command_lifecycle.py", "command_binding.py"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    environment = {key: value for key, value in os.environ.items() if key != "PYTHONDONTWRITEBYTECODE"}
    result = subprocess.run(
        [sys.executable, str(scripts / "recover_command_lifecycle.py"), "--help"],
        env=environment, capture_output=True, text=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
    )
    assert result.returncode == 0, result.stdout + result.stderr
    assert not list(tmp_path.rglob("__pycache__"))


@pytest.mark.skipif(os.name != "nt", reason="Native profile fixture is Windows-specific")
def test_real_helper_inspection_repair_and_retry_contract(interrupted):
    arguments, path = interrupted
    payload = arguments["payload_root"]
    scripts = payload / "scripts"
    scripts.mkdir()
    for name in ("recover_command_lifecycle.py", "command_binding.py", "command_lifecycle.py"):
        shutil.copyfile(ROOT / "scripts" / name, scripts / name)
    shutil.copytree(ROOT / "scripts" / "installation-context", scripts / "installation-context")
    command = [
        sys.executable, str(scripts / "recover_command_lifecycle.py"),
        "inspect", "--payload-root", str(payload), "--runtime-root", str(path.parent),
        "--project-root", str(arguments["project_root"]),
    ]
    environment = {
        key: value for key, value in arguments["environment"].items()
        if key != "PYTHONDONTWRITEBYTECODE"
    }
    before = path.read_bytes()
    foreign_helper = subprocess.run(
        [command[0], str(ROOT / "scripts" / "recover_command_lifecycle.py"), *command[2:]],
        env=environment, capture_output=True, text=True,
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert foreign_helper.returncode == 2
    assert "does not own this recovery helper" in foreign_helper.stderr
    assert path.read_bytes() == before
    plan = subprocess.run(
        command, env=environment, capture_output=True, text=True,
        timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert plan.returncode == 0, plan.stdout + plan.stderr
    evidence = json.loads(plan.stdout)["expectedEvidence"]
    assert path.read_bytes() == before
    assert not list(payload.rglob("__pycache__"))
    command[2] = "repair"
    refused = subprocess.run(
        [*command, "--expected-evidence", "changed"], env=environment,
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert refused.returncode == 3, refused.stdout + refused.stderr
    assert "evidence changed" in refused.stderr
    assert path.read_bytes() == before
    assert not list(payload.rglob("__pycache__"))
    repaired = subprocess.run(
        [*command, "--expected-evidence", evidence], env=environment,
        capture_output=True, text=True, timeout=30, creationflags=subprocess.CREATE_NO_WINDOW,
    )
    assert repaired.returncode == 0, repaired.stdout + repaired.stderr
    assert json.loads(repaired.stdout)["commandReady"] is False
    with binding._load_lifecycle().legacy_lifecycle(binding._rules(), primitive, **arguments):
        binding.write_legacy_manifest(
            primitive, json.loads(path.read_text()), **arguments,
        )
    assert binding.resolve_command_binding(primitive, **arguments)["status"] == "selected"
