"""Installation-owner binding controls; no enrollment or native/SDK proof."""

from __future__ import annotations

import json
import os
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from agent_bridge import _installation_context as installation_context
from agent_bridge.db import Database
from agent_bridge.loop_governance import LoopGovernance
from agent_bridge.runtime_custody import RuntimeCustodyError

PAYLOAD = Path(__file__).resolve().parents[1]


@pytest.fixture(params=["legacy", "namespaced"])
def installation(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, request: pytest.FixtureRequest,
) -> dict[str, Any]:
    home = tmp_path / "home"
    root = home / ".agent-bridge"
    root.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(home))
    monkeypatch.setenv("USERPROFILE", str(home))
    if os.name != "nt":
        import pwd

        monkeypatch.setattr(pwd, "getpwuid", lambda uid: SimpleNamespace(pw_dir=str(home)))
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(root))
    monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(root))
    monkeypatch.delenv("COPILOT_PLUGIN_ROOT", raising=False)
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    environment = {"HOME": str(home)}
    if installation_context._is_wsl_guest():
        # The isolated runner deliberately strips the real distribution name.
        # Bind only this disposable profile to a synthetic direct-entry name.
        monkeypatch.setenv("WSL_DISTRO_NAME", "SyntheticTestGuest")
        environment["WSL_DISTRO_NAME"] = "SyntheticTestGuest"
    context = installation_context.stamp_context(
        payload_root=PAYLOAD, plugin_id="agent-bridge",
        source_descriptor={"kind": "github", "repo": "example/extensions"},
        durable_home=home / ".copilot-extensions", copilot_home=home / ".copilot",
        payload_version="1.0.0", payload_origin="explicit",
        expected_namespace_generation=0, expected_install_generation=0,
        environment=environment,
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", context["installReceipt"])
    if request.param == "namespaced":
        (home / ".copilot-extensions" / "installation-mode.json").write_text(
            json.dumps({
                "schema": installation_context.POLICY_SCHEMA, "version": 1,
                "installationMode": {"enabled": True},
            }), encoding="utf-8",
        )
        activation = installation_context.compare_and_swap_activation(
            context=context["installReceipt"], durable_home=home / ".copilot-extensions",
            expected_marketplace_id=context["marketplaceId"], expected_plugin_id="agent-bridge",
            expected_namespace_generation=context["namespaceGeneration"],
            expected_install_generation=context["generation"], expected_activation_generation=0,
            activation_mode="namespaced", activation_state="active",
            legacy_disposition="absent", legacy_root=root,
            legacy_probe={"declared": True, "result": "absent", "checkedAt": None},
        )
        assert activation["status"] == "ready"
        root = Path(context["pluginRoot"])
        monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(root))
        monkeypatch.setenv("AGENT_BRIDGE_CONFIG_DIR", str(root))
    (root / "deploy-manifest.json").write_text(
        json.dumps({"source": {"path": str(PAYLOAD)}}), encoding="utf-8",
    )
    return {"root": root, "context": context, "home": home}


def test_actual_installation_owner_derives_binding_without_custody_mutation(
    installation: dict[str, Any], tmp_db: Database,
) -> None:
    governance = LoopGovernance()
    before = {
        path.relative_to(installation["home"]): path.read_bytes()
        for path in installation["home"].rglob("*") if path.is_file()
    }
    binding = governance.runtime_installation(tmp_db)
    assert binding.identity == installation["context"]["installReceipt"]
    assert binding.source_fingerprint == installation["context"]["sourceFingerprint"]
    assert binding.runtime_root == str(installation["root"].resolve())
    assert binding.database == str(tmp_db.db_path.absolute())
    assert governance.runtime_installation(tmp_db) == binding
    assert {
        path.relative_to(installation["home"]): path.read_bytes()
        for path in installation["home"].rglob("*") if path.is_file()
    } == before
    assert tmp_db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    assert tmp_db.execute_read("SELECT * FROM bridge_runtime_producers") == []
    assert tmp_db.execute_read("SELECT * FROM live_messages") == []
    # The derived association is accepted by the actual DB owner, not just a
    # similar-looking serialized installation value.
    epoch = tmp_db.initialize_runtime_epoch(binding)
    assert epoch.installation == binding.document()


@pytest.mark.parametrize("helper", [None, {"error": {"reason": "unavailable"}}, lambda _: {}])
def test_missing_or_synthetic_governance_cannot_supply_installation(
    helper: Any, tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    governance = LoopGovernance(helper=helper)
    with pytest.raises(RuntimeCustodyError, match="unavailable"):
        governance.runtime_installation(tmp_db)
    assert tmp_db.execute_read("SELECT * FROM bridge_runtime_epoch") == []


@pytest.mark.parametrize("field", [
    "payload", "source", "generation", "install_state", "namespace_state",
])
def test_changed_receipt_refuses_without_rebinding(
    installation: dict[str, Any], tmp_db: Database, field: str,
) -> None:
    governance = LoopGovernance()
    binding = governance.runtime_installation(tmp_db)
    context_path = Path(binding.identity)
    if field in ("source", "namespace_state"):
        context_path = Path(installation["context"]["namespaceReceipt"])
    raw = json.loads(context_path.read_text(encoding="utf-8"))
    if field == "payload":
        other = installation["home"] / "other-payload"
        other.mkdir()
        raw["payload"]["root"] = str(other)
    elif field == "source":
        raw["source"]["fingerprint"] = "sha256:" + "f" * 64
    elif field in ("install_state", "namespace_state"):
        raw["state"] = "inactive"
    else:
        raw["generation"] += 1
    context_path.write_text(json.dumps(raw), encoding="utf-8")
    with pytest.raises(RuntimeCustodyError):
        governance.runtime_installation(tmp_db)
    assert governance._installation == binding
    assert tmp_db.execute_read("SELECT * FROM bridge_runtime_epoch") == []


def test_other_daemon_root_cannot_claim_the_context(
    installation: dict[str, Any], tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
    tmp_path: Path,
) -> None:
    governance = LoopGovernance()
    other = tmp_path / "other-runtime"
    other.mkdir()
    monkeypatch.setenv("AGENT_BRIDGE_INSTALL_DIR", str(other))
    with pytest.raises(RuntimeCustodyError, match="does not own"):
        governance.runtime_installation(tmp_db)


@pytest.mark.parametrize("manifest", [{}, [], {"source": {"path": "."}}])
def test_changed_deployment_manifest_refuses(
    installation: dict[str, Any], tmp_db: Database, manifest: Any,
) -> None:
    governance = LoopGovernance()
    (installation["root"] / "deploy-manifest.json").write_text(
        json.dumps(manifest), encoding="utf-8",
    )
    with pytest.raises(RuntimeCustodyError):
        governance.runtime_installation(tmp_db)
    assert tmp_db.execute_read("SELECT * FROM bridge_runtime_epoch") == []


def test_database_cannot_change_under_the_same_governance_owner(
    installation: dict[str, Any], tmp_db: Database, tmp_path: Path,
) -> None:
    governance = LoopGovernance()
    binding = governance.runtime_installation(tmp_db)
    other = Database(tmp_path / "other.db")
    try:
        with pytest.raises(RuntimeCustodyError, match="database binding changed"):
            governance.runtime_installation(other)
        assert governance._installation == binding
        assert other.execute_read("SELECT * FROM bridge_runtime_epoch") == []
    finally:
        other.close()


def test_maintenance_after_first_resolution_refuses(
    installation: dict[str, Any], tmp_db: Database,
) -> None:
    governance = LoopGovernance()
    governance.runtime_installation(tmp_db)
    marker = installation["home"] / ".copilot-extensions" / "maintenance"
    marker.write_text("", encoding="utf-8")
    with pytest.raises(RuntimeCustodyError, match="not current"):
        governance.runtime_installation(tmp_db)


def test_generation_change_during_resolution_cannot_publish_binding(
    installation: dict[str, Any], tmp_db: Database, monkeypatch: pytest.MonkeyPatch,
) -> None:
    governance = LoopGovernance()
    module = governance._helper["module"]
    original = module.resolve_installation_mode
    calls = 0

    def change_generation(**kwargs: Any) -> dict[str, Any]:
        nonlocal calls
        result = original(**kwargs)
        calls += 1
        if calls == 2:
            context_path = Path(installation["context"]["installReceipt"])
            raw = json.loads(context_path.read_text(encoding="utf-8"))
            raw["generation"] += 1
            context_path.write_text(json.dumps(raw), encoding="utf-8")
        return result

    monkeypatch.setattr(module, "resolve_installation_mode", change_generation)
    with pytest.raises(RuntimeCustodyError, match="changed during resolution"):
        governance.runtime_installation(tmp_db)
    assert governance._installation is None
    assert tmp_db.execute_read("SELECT * FROM bridge_runtime_epoch") == []


def test_preexisting_legacy_transfer_does_not_become_ownership_from_a_new_baseline(
    installation: dict[str, Any], tmp_db: Database,
) -> None:
    home = installation["home"]
    durable = home / ".copilot-extensions"
    destination = installation_context.stamp_context(
        payload_root=PAYLOAD, plugin_id="agent-bridge",
        source_descriptor={"kind": "github", "repo": "example/other-extensions"},
        durable_home=durable, copilot_home=home / ".copilot",
        payload_version="1.0.0", payload_origin="explicit",
        expected_namespace_generation=0, expected_install_generation=0,
        environment={"HOME": str(home)},
    )
    namespaced = installation["root"] == Path(installation["context"]["pluginRoot"])
    (durable / "installation-mode.json").write_text(
        json.dumps({
            "schema": installation_context.POLICY_SCHEMA, "version": 1,
            "installationMode": {
                "enabled": namespaced,
                "marketplaces": {destination["marketplaceId"]: {"enabled": True}},
            },
        }), encoding="utf-8",
    )
    activation = installation_context.compare_and_swap_activation(
        context=destination["installReceipt"], durable_home=durable,
        expected_marketplace_id=destination["marketplaceId"], expected_plugin_id="agent-bridge",
        expected_namespace_generation=destination["namespaceGeneration"],
        expected_install_generation=destination["generation"], expected_activation_generation=0,
        activation_mode="namespaced", activation_state="active", legacy_disposition="retained-inert",
        legacy_root=home / ".agent-bridge",
        legacy_probe={"declared": True, "result": "absent", "checkedAt": None},
    )
    assert activation["status"] == "ready"
    activation_path = Path(destination["pluginRoot"]) / "installation-activation.json"
    active = json.loads(activation_path.read_text(encoding="utf-8"))
    (home / ".agent-bridge" / ".installation-ownership.json").write_text(
        json.dumps({
            "schema": installation_context.TOMBSTONE_SCHEMA, "version": 1,
            "marketplaceId": destination["marketplaceId"], "pluginId": "agent-bridge",
            "environment": active["environment"],
            "activation": {"path": str(activation_path), "generation": active["generation"]},
            "transferredAt": active["updatedAt"],
        }), encoding="utf-8",
    )
    governance = LoopGovernance()
    # A current loop baseline alone is not a legacy mutation-ownership grant.
    verdict = governance.recheck("after-existing-transfer")
    assert verdict["status"] == "ready"
    assert verdict["legacy"]["disposition"] == "owned-by-other-cell"
    if namespaced:
        assert governance.runtime_installation(tmp_db).runtime_root == str(installation["root"])
    else:
        assert verdict["actualMode"] == "legacy"
        with pytest.raises(RuntimeCustodyError, match="does not own"):
            governance.runtime_installation(tmp_db)
    assert tmp_db.execute_read("SELECT * FROM bridge_runtime_epoch") == []
