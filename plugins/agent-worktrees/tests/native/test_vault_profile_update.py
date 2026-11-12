"""Collected prepared-source update against Vault's real native profile owner."""

from __future__ import annotations

import dataclasses
import importlib
import json
import os
from pathlib import Path
import runpy
import shutil
import sys
import types

import pytest

ROOT = Path(__file__).resolve().parents[4]


@pytest.fixture
def vault_inputs(tmp_path_factory, monkeypatch):
    name = "vault_profile_update_fixtures"
    namespace = types.ModuleType(name)
    namespace.__path__ = [str(ROOT / "plugins" / "agent-vault" / "tests")]
    monkeypatch.setitem(sys.modules, name, namespace)
    fixtures = importlib.import_module(name + ".native.test_runtime_profile")
    yield from fixtures.native_profile_inputs(tmp_path_factory, crypto=False)


@pytest.mark.skipif(os.name != "nt", reason="Windows native collected update")
@pytest.mark.timeout(180)
def test_collected_vault_source_update_recognizes_profile_and_completion(
    vault_inputs, monkeypatch,
):
    from agent_worktrees import reconcile, update_runtime, update_runtime_plan
    from agent_worktrees.update_runtime_source import PreparedRuntimeSource

    payload, home, environment, shell = vault_inputs
    monkeypatch.syspath_prepend(str(ROOT / "tools"))
    release = runpy.run_path(str(ROOT / "tools" / "materialize_main.py"))
    materialized = release["materialize_installer_engine_ref_into"](
        source_consumer_dir=ROOT / "plugins" / "agent-vault", dest_consumer_dir=payload,
        canonical_root=ROOT,
    )
    assert not any(message.startswith("SKIP ") for message in materialized), materialized
    owner = home / ".copilot" / "installed-plugins" / "example" / "agent-vault"
    owner.parent.mkdir(parents=True)
    shutil.copytree(payload, owner)
    settings = home / ".github" / "copilot" / "settings.json"
    settings.parent.mkdir(parents=True)
    settings.write_text(json.dumps({
        "extraKnownMarketplaces": {
            "example": {"source": {"source": "github", "repo": "Example/Plugins"}},
        },
    }))
    environment.update({
        "COPILOT_PLUGIN_ROOT": str(owner), "COPILOT_PLUGIN_STAGED_FROM": str(owner),
        "COPILOT_EXTENSIONS_INSTALL_PROJECT_ROOT": str(home),
    })
    root = home / ".agent-vault"
    root.mkdir(exist_ok=True)
    (root / "runtime-profile.json").write_text(json.dumps({
        "schema": "agent-vault.runtime-profile", "version": 1, "profile": "base",
    }))
    monkeypatch.syspath_prepend(str(payload / "scripts"))
    import versioned_runtime_source as source

    version = source.payload_package_version(payload, "agent-vault")
    digest = source.assembly_hash(payload)
    identity = source.SourceIdentity(
        "agent-vault", version, "a" * 40, "b" * 40, digest,
        source.runtime_version(version, "a" * 40, digest),
    )
    (payload / source.SOURCE_FILE).write_text(json.dumps(identity.document()))
    prepared = PreparedRuntimeSource(payload, json.dumps(source.manifest_fields(identity)))
    monkeypatch.setattr(reconcile, "core_installed_payload_dir", lambda _name: owner)
    monkeypatch.setattr(
        reconcile, "runtime_installer_environment", lambda *_a: (environment.copy(), root),
    )
    monkeypatch.setattr(update_runtime, "_machine_gate_blocks", lambda *_a: False)
    step = update_runtime_plan.plan_registered(
        "agent-vault", "windows", force=False, gate_context=home, source=prepared,
    )
    assert not step.status, step.status
    assert step.source_target.profile == "base"
    assert step.source_target.version != identity.runtime_version
    step = dataclasses.replace(step, argv=(*step.argv, "-NoService"))
    assert update_runtime._execute_runtime_step(step) == "OK"
    assert prepared.deployed_version(root, owner, target=step.source_target) == step.source_target.version
    assert update_runtime_plan.plan_registered(
        "agent-vault", "windows", force=False, gate_context=home, source=prepared,
    ).status == "SKIPPED (current)"
    slot = root / "versions" / step.source_target.version
    receipt = slot / "runtime-profile.json"
    value = json.loads(receipt.read_text())
    value["payloadHash"] = "f" * 64
    receipt.write_text(json.dumps(value))
    assert prepared.deployed_version(root, owner, target=step.source_target) is None
