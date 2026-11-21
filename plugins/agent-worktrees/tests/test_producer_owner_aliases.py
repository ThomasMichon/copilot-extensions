"""Accepted historical actors and strict receiving-record ownership views."""

from __future__ import annotations

import copy
import subprocess
from pathlib import Path
from typing import Any

import pytest
import yaml
from test_resource_producer_handoff_recovery import historical_producer as historical_producer

from agent_worktrees import claim_handoffs as handoffs
from agent_worktrees import claim_providers, config as cfg, installer, producer_view_scan, tracking
from agent_worktrees.producer_view_scan import scan_ownership
from agent_worktrees.tracking_producer_write import apply_resource_producer
from agent_worktrees.tracking_lifecycle import create_new_record


def _prepare_scan(path: Path, monkeypatch: pytest.MonkeyPatch) -> Path:
    root = path.parent.parent.parent
    registry = root / "projects.yaml"
    registry.write_text("projects:\n  p: {}\n", encoding="utf-8")
    monkeypatch.setattr(installer, "projects_yaml_path", lambda: registry)
    return root


def _observe_without_effects(
    root: Path, monkeypatch: pytest.MonkeyPatch,
) -> dict[str, Any]:
    before = {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    with monkeypatch.context() as patch:
        patch.setattr(tracking, "load_record", lambda *a, **k: pytest.fail("cached record read"))
        patch.setattr(tracking, "save_record", lambda *a, **k: pytest.fail("snapshot wrote"))
        patch.setattr(claim_providers, "discover_claim_providers", lambda *a, **k: pytest.fail("provider discovery"))
        patch.setattr(claim_providers, "resolve_claim_status", lambda *a, **k: pytest.fail("provider callback"))
        patch.setattr(cfg, "load_config", lambda *a, **k: pytest.fail("full configuration load"))
        patch.setattr(cfg, "load_project_config", lambda *a, **k: pytest.fail("full project load"))
        patch.setattr(subprocess, "run", lambda *a, **k: pytest.fail("snapshot spawned"))
        snapshot = scan_ownership()
    assert before == {path: path.read_bytes() for path in root.rglob("*") if path.is_file()}
    return snapshot


def test_accepted_legacy_consumer_projects_the_verified_record(
    historical_producer: tuple[Path, handoffs.ClaimBundle, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, bundle, _ = historical_producer
    root = _prepare_scan(path, monkeypatch)
    accepted = handoffs.accept(
        bundle.bundle_id, actor="example-host-wsl/p/consumer", machine="example-host-wsl",
    )
    assert accepted.state == "accepted"
    record = tracking.load_record(path)
    metadata = record.resources[0].producer
    assert isinstance(metadata, dict)
    assert metadata["transfer"]["consumer"] == bundle.consumer
    config = cfg.load_config()
    checkout = Path(config.repos["p"].worktree_root) / "independent"
    checkout.mkdir()
    create_new_record(
        "independent", "worktree/independent", str(checkout), "p",
        "example-host-wsl", "wsl", path.parent,
    )
    reserved = apply_resource_producer({
        "schema": "copilot-extensions.resource-producer", "version": 1,
        "yaml_path": str(path.with_name("independent.yaml")),
        "owner_ref": "example-host-wsl/p/independent", "operation": "reserve",
        "input": {"kind": "container", "namespace": "raw-resource", "recovery": {}},
    })
    assert reserved.get("ok"), reserved
    snapshot = _observe_without_effects(root, monkeypatch)
    assert snapshot["complete"], snapshot["issues"]
    assert {
        row["record_owner"]: row["current_owner"] for row in snapshot["entries"]
    } == {
        "example-host-wsl/p/owner": "example-host-wsl/p/consumer",
        "example-host-wsl/p/consumer": "example-host-wsl/p/consumer",
        "example-host-wsl/p/independent": "example-host-wsl/p/independent",
    }


@pytest.mark.parametrize("consumer", [
    "foreign/p/consumer", "unverified/p/consumer",
    "example-host-wsl/q/consumer", "example-host-wsl/p/other",
])
def test_unverified_consumer_cannot_borrow_a_receiving_subject(
    historical_producer: tuple[Path, handoffs.ClaimBundle, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch, consumer: str,
) -> None:
    path, bundle, _ = historical_producer
    root = _prepare_scan(path, monkeypatch)
    handoffs.accept(
        bundle.bundle_id, actor="example-host-wsl/p/consumer", machine="example-host-wsl",
    )
    (root / "anchor" / "machines.yaml").write_text(
        "machines:\n  example-host: {}\n  example-host-wsl: {}\n"
        "  foreign: {hostname: elsewhere, execution_platform: windows}\n",
        encoding="utf-8",
    )
    assert not handoffs._same_worktree("example-host-wsl/p/consumer", consumer)
    data = yaml.safe_load(path.read_text(encoding="utf-8"))
    data["resources"][0]["producer"]["transfer"]["consumer"] = consumer
    path.write_text(yaml.safe_dump(data), encoding="utf-8")
    snapshot = _observe_without_effects(root, monkeypatch)
    assert not snapshot["complete"], snapshot
    assert all(row["current_owner"] is None for row in snapshot["entries"])


@pytest.mark.parametrize("damage", ["missing", "unaccepted", "actor", "identity", "duplicate", "malformed"])
def test_accepted_evidence_is_required_for_aliases_not_literal_actors(
    historical_producer: tuple[Path, handoffs.ClaimBundle, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch, damage: str,
) -> None:
    path, bundle, _ = historical_producer
    root = _prepare_scan(path, monkeypatch)
    handoffs.accept(
        bundle.bundle_id, actor="example-host-wsl/p/consumer", machine="example-host-wsl",
    )
    registry = handoffs.registry_path()
    data = yaml.safe_load(registry.read_text(encoding="utf-8"))
    if damage == "missing":
        registry.unlink()
    else:
        accepted = data["bundles"][0]
        if damage == "unaccepted":
            accepted["state"] = "offered"
        elif damage == "actor":
            accepted["consumer"] = "unverified/p/consumer"
        elif damage == "identity":
            accepted["claims"][0]["producer"]["identity"] = {"immutable_id": "replacement"}
        elif damage == "duplicate":
            data["bundles"].append(copy.deepcopy(accepted))
        else:
            data = {"version": 999, "bundles": []}
        registry.write_text(yaml.safe_dump(data), encoding="utf-8")
    snapshot = _observe_without_effects(root, monkeypatch)
    if bundle.consumer == "example-host-wsl/p/consumer":
        assert snapshot["complete"], snapshot["issues"]
        assert {row["current_owner"] for row in snapshot["entries"]} == {
            "example-host-wsl/p/consumer",
        }
    else:
        assert not snapshot["complete"], snapshot
        assert all(row["current_owner"] is None for row in snapshot["entries"])


def test_accepted_evidence_replacement_is_detected_without_hiding_other_changes(
    historical_producer: tuple[Path, handoffs.ClaimBundle, dict[str, Any]],
    monkeypatch: pytest.MonkeyPatch,
) -> None:
    path, bundle, _ = historical_producer
    root = _prepare_scan(path, monkeypatch)
    handoffs.accept(
        bundle.bundle_id, actor="example-host-wsl/p/consumer", machine="example-host-wsl",
    )
    registry = handoffs.registry_path()
    before = {item: item.read_bytes() for item in root.rglob("*") if item.is_file()}
    read = producer_view_scan._read
    reads = 0

    def replace_evidence(target: Path) -> tuple[object, tuple[int, ...]]:
        nonlocal reads
        data, stamp = read(target)
        if target == registry:
            reads += 1
            registry.write_bytes(before[registry] + b"\n# concurrent evidence replacement\n")
        return data, stamp

    monkeypatch.setattr(producer_view_scan, "_read", replace_evidence)
    snapshot = scan_ownership()
    if bundle.consumer == "example-host-wsl/p/consumer":
        assert reads == 0
        assert snapshot["complete"], snapshot["issues"]
    else:
        assert reads == 1
        assert not snapshot["complete"]
        assert "snapshot-changed" in {item["code"] for item in snapshot["issues"]}
        assert all(row["current_owner"] is None for row in snapshot["entries"])
    assert {
        item: item.read_bytes() for item in root.rglob("*") if item.is_file() and item != registry
    } == {item: value for item, value in before.items() if item != registry}
