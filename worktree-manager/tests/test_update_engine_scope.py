"""A valid managed cell must retain its scope through fresh bootstrap repair."""

import json
import os
from pathlib import Path

import pytest
from test_update_engine_repair import repair_case as shared_repair_case

repair_case = shared_repair_case
pytestmark = pytest.mark.skipif(
    os.environ.get("COPILOT_EXTENSIONS_ALLOW_EXPLICIT_TEST_TIERS") != "1",
    reason="real scoped installer/publication policy lane; not promotion-unit evidence",
)


def _activate_scope(case, monkeypatch):
    primitive = case.primitive
    environment = dict(os.environ)
    durable = case.home / ".copilot-extensions"
    stamped = primitive.stamp_context(
        payload_version="1.0.0", payload_origin="installed",
        expected_namespace_generation=0, expected_install_generation=0,
        payload_root=case.incumbent, plugin_id="agent-worktrees",
        environment=environment, durable_home=durable,
    )
    primitive.compare_and_swap_activation(
        context=stamped["installReceipt"],
        expected_marketplace_id=stamped["marketplaceId"],
        expected_plugin_id="agent-worktrees",
        expected_namespace_generation=stamped["namespaceGeneration"],
        expected_install_generation=stamped["generation"],
        expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active",
        legacy_disposition="retained-inert",
        legacy_probe={"declared": True, "result": "present", "checkedAt": "2026-01-01T00:00:00Z"},
        legacy_root=case.runtime, environment=environment, durable_home=durable,
    )
    (durable / "installation-mode.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": True, "marketplaces": {}},
    }), encoding="utf-8")
    context = stamped["installReceipt"]
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context)
    arguments = {
        "payload_root": case.incumbent, "plugin_id": "agent-worktrees",
        "legacy_root": case.runtime, "context": context, "durable_home": durable,
        "environment": dict(os.environ),
    }
    decision = primitive.resolve_runtime_deployment(**arguments)
    assert decision["status"] == "ready" and decision["actualMode"] == "namespaced"
    root = Path(decision["runtimeRoot"])
    assert root != case.runtime
    (root / "runtime-deployment.json").write_text(json.dumps({
        "schema": primitive.RUNTIME_DEPLOYMENT_SCHEMA, "version": 1, "managed": True,
        "owner": primitive._deployment_owner(decision, arguments),
    }), encoding="utf-8")
    return root, durable, Path(context)


def test_valid_scoped_update_reaches_only_its_own_construction(
    repair_case, monkeypatch, capsys,
):
    case = repair_case
    root, _durable, _context = _activate_scope(case, monkeypatch)
    before = case.manifest.read_bytes()
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), f"rc={rc}; {capsys.readouterr().err}"
    assert case.calls
    assert str(root) in case.calls[-1][0]
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()


@pytest.mark.parametrize("source_state", [
    "present", "missing-payload", "legacy-unbound", "legacy-snapshot",
])
def test_automatic_cell_selection_is_preserved_without_an_inherited_context(
    repair_case, monkeypatch, capsys, source_state,
):
    case = repair_case
    snapshot = None
    if source_state == "legacy-snapshot":
        from test_update_engine_snapshot import _historical_snapshot

        snapshot = _historical_snapshot(case)
    root, _durable, _context = _activate_scope(case, monkeypatch)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT")
    automatic = case.primitive.resolve_runtime_deployment(
        payload_root=case.incumbent, plugin_id="agent-worktrees",
        legacy_root=case.runtime, environment=dict(os.environ),
    )
    assert automatic["actualMode"] == "namespaced" and automatic["runtimeRoot"] == str(root)
    if source_state == "missing-payload":
        case.incumbent.rename(case.incumbent.with_name("retained-payload"))
    elif source_state in {"legacy-unbound", "legacy-snapshot"}:
        from test_update_engine_repair import _legacy_publication

        _legacy_publication(case)
        if snapshot is not None:
            manifest = json.loads(case.manifest.read_text())
            manifest["source"]["path"] = str(snapshot)
            case.manifest.write_text(json.dumps(manifest), encoding="utf-8")
    before = case.manifest.read_bytes()
    rc = case.update.repair_engine(case.source)
    assert case.effect.is_file(), f"rc={rc}; {capsys.readouterr().err}"
    assert str(root) in case.calls[-1][0]
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()
    assert "COPILOT_EXTENSIONS_CONTEXT" not in os.environ


@pytest.mark.parametrize("damage", [
    "foreign-source", "stale-install", "deactivated", "invalid-policy", "foreign-owner",
])
@pytest.mark.parametrize("explicit_context", [True, False], ids=["explicit", "automatic"])
def test_invalid_scoped_update_never_constructs_or_falls_back(
    repair_case, monkeypatch, damage, explicit_context,
):
    case = repair_case
    root, durable, context = _activate_scope(case, monkeypatch)
    if not explicit_context:
        monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT")
    if damage == "foreign-source":
        namespace = root.parent.parent / "namespace.json"
        value = json.loads(namespace.read_text())
        value["source"]["fingerprint"] = "sha256:" + "0" * 64
        namespace.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "stale-install":
        value = json.loads(context.read_text())
        value["generation"] += 1
        context.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "deactivated":
        activation = root / "installation-activation.json"
        value = json.loads(activation.read_text())
        value.update(mode="legacy", state="deactivated")
        activation.write_text(json.dumps(value), encoding="utf-8")
    elif damage == "invalid-policy":
        (durable / "installation-mode.json").write_text("{invalid", encoding="utf-8")
    else:
        deployment = root / "runtime-deployment.json"
        value = json.loads(deployment.read_text())
        value["owner"]["sourceFingerprint"] = "sha256:" + "0" * 64
        deployment.write_text(json.dumps(value), encoding="utf-8")
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert not case.effect.exists()
    assert not case.calls
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()
    assert not (root / "bin").exists()


def test_deactivation_required_cell_retains_management_repair(repair_case, monkeypatch):
    case = repair_case
    root, durable, _context = _activate_scope(case, monkeypatch)
    (durable / "installation-mode.json").write_text(json.dumps({
        "schema": "copilot-extensions.installation-mode", "version": 1,
        "installationMode": {"enabled": False, "marketplaces": {}},
    }), encoding="utf-8")
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert case.effect.is_file()
    assert str(root) in case.calls[-1][0]
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()


def test_scoped_activation_change_during_repair_is_not_admitted(repair_case, monkeypatch):
    case = repair_case
    root, _durable, _context = _activate_scope(case, monkeypatch)
    incumbent = case.update._incumbent
    reads = []

    def change_activation(source, environment, payload):
        selected = incumbent(source, environment, payload)
        reads.append(selected)
        if len(reads) == 1:
            activation = root / "installation-activation.json"
            value = json.loads(activation.read_text())
            value["generation"] += 1
            activation.write_text(json.dumps(value), encoding="utf-8")
        return selected

    monkeypatch.setattr(case.update, "_incumbent", change_activation)
    before = case.manifest.read_bytes()
    assert case.update.repair_engine(case.source) != 0
    assert len(reads) == 2
    assert not case.calls
    assert not case.effect.exists()
    assert case.manifest.read_bytes() == before
    assert not (case.runtime / "bin").exists()
