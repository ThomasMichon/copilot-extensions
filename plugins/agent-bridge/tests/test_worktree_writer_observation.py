"""Cheap ASGI discovery observes selection, not the delegated serializer."""

from __future__ import annotations

import os
import json
import shutil
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from zdd import routing

from agent_bridge import session_lifecycle_cli as lifecycle
from agent_bridge import _installation_context as installation
from agent_bridge.routes import health


@pytest.mark.parametrize("selection", ["legacy", "cell", "path", "missing", "refused"])
def test_health_observes_canonical_writer_selection_without_launch(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, selection: str,
) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    own = tmp_path / ".agent-bridge"
    peer = tmp_path / ".agent-worktrees"
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: own)
    monkeypatch.setattr(
        lifecycle, "_legacy_worktrees_launch_prefix",
        lambda root: ["legacy-owner"] if selection == "legacy" else None,
    )
    monkeypatch.setattr(
        lifecycle.shutil, "which", lambda name: "/compat/owner" if selection == "path" else None,
    )
    if selection in {"cell", "refused"}:
        own = tmp_path / "marketplaces" / "fixture" / "plugins" / "agent-bridge"
        peer = own.parent / "agent-worktrees"
        peer.mkdir(parents=True)
        monkeypatch.setattr(lifecycle._peer_launch, "validate_owner", Mock(
            return_value={"cellRoot": str(own.parent.parent), "pluginRoot": str(own)},
            side_effect=ValueError("invalid owner") if selection == "refused" else None,
        ))
        monkeypatch.setattr(lifecycle._peer_launch, "launch_prefix", lambda *args: ["cell-owner"])
        monkeypatch.setattr(lifecycle.shutil, "which", lambda name: pytest.fail("cell used PATH"))
    monkeypatch.setattr(
        lifecycle.subprocess, "run", lambda *a, **k: pytest.fail("health launched a child"),
    )
    monkeypatch.setattr(health, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        health, "get_default_manager", lambda: SimpleNamespace(carrier_diagnostics=lambda: {}),
    )
    routing.publish_active(tmp_path, bind="127.0.0.1", port=12345, pid=os.getpid(), version="fixture")
    app = FastAPI()
    app.include_router(health.router)
    with TestClient(app) as client:
        before = client.get("/health").json()
        assert before["status"] == "ok"
        assert before["capabilities"]["worktree_record_delegation"] == 1
        assert before["worktree_record_writer"] == {
            "installation_root": str(peer) if selection in {"legacy", "cell"} else None,
        }
        assert before["slot"]["role"] == "active"
        assert before["slot"]["pid"] == os.getpid()
        assert type(before["slot"]["active"]["generation"]) is int
        assert before["slot"]["active"]["generation"] > 0
        assert "writer_contract" not in before["worktree_record_writer"]
        routing.publish_active(tmp_path, bind="127.0.0.1", port=12346, pid=999999, version="fixture")
        after = client.get("/health").json()
        assert after["slot"]["role"] == "passive"
        assert after["slot"]["active"]["generation"] != before["slot"]["active"]["generation"]
        assert after["worktree_record_writer"] == before["worktree_record_writer"]


def test_repeated_health_reuses_real_resolver_but_refreshes_selected_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(lifecycle, "_legacy_runtime_modules", {})
    own = tmp_path / ".agent-bridge"
    peer = tmp_path / ".agent-worktrees"
    scripts = own / "scripts"
    scripts.mkdir(parents=True)
    for source in (Path(__file__).resolve().parents[1] / "scripts").glob("versioned_runtime*.py"):
        shutil.copyfile(source, scripts / source.name)
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: own)
    monkeypatch.setattr(lifecycle.shutil, "which", lambda name: pytest.fail("lost installed owner"))
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda *a, **k: pytest.fail("health launched a child"))
    monkeypatch.setattr(health, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        health, "get_default_manager", lambda: SimpleNamespace(carrier_diagnostics=lambda: {}),
    )
    registrations = []
    register = getattr(os, "register_at_fork", None)
    if register is not None:
        def remember(**kwargs: object) -> None:
            registrations.append(kwargs)
            register(**kwargs)

        monkeypatch.setattr(os, "register_at_fork", remember)

    def select(version: str) -> Path:
        slot = peer / "versions" / version
        python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"selected interpreter fixture")
        (slot / ".install-complete.json").write_text(
            json.dumps({"version": version, "completed_at": "fixture", "pid": 0}),
            encoding="utf-8",
        )
        (peer / "current-version").write_text(version, encoding="utf-8")
        return python

    first = select("1.0.0")
    app = FastAPI()
    app.include_router(health.router)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["worktree_record_writer"]["installation_root"] == str(peer)
        initial = len(registrations)
        assert initial > 0 if register is not None else initial == 0
        assert lifecycle._legacy_worktrees_runtime_prefix(own)[0] == str(first)
        for _ in range(32):
            assert client.get("/health").json()["worktree_record_writer"]["installation_root"] == str(peer)
        second = select("2.0.0")
        assert client.get("/health").status_code == 200
        assert lifecycle._legacy_worktrees_runtime_prefix(own)[0] == str(second)
        assert len(registrations) == initial


def test_repeated_health_retains_packaged_validator_but_rechecks_cell_receipts(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch,
) -> None:
    monkeypatch.delenv("COPILOT_EXTENSIONS_CONTEXT", raising=False)
    monkeypatch.setattr(Path, "home", lambda: tmp_path)
    monkeypatch.setattr(lifecycle._peer_launch, "_packaged_primitives", {})
    cell = tmp_path / "marketplaces" / "example--9caa0da95f327099"
    own = cell / "plugins" / "agent-bridge"
    own.mkdir(parents=True)
    (own.parent / "agent-worktrees").mkdir()
    payload = cell / "payloads" / "agent-bridge"
    payload.mkdir(parents=True)
    namespace = cell / "namespace.json"
    namespace.write_text(json.dumps({
        "schema": "copilot-extensions.marketplace-namespace", "version": 1,
        "marketplaceId": cell.name, "generation": 1, "state": "active",
        "source": {
            "kind": "github", "canonical": "github:example-org/example-marketplace",
            "ref": "", "fingerprint":
                "sha256:9caa0da95f327099983348a3d2353fa93e57ef6428b69f354cc3bdc76ada0894",
        },
        "locators": [],
    }), encoding="utf-8")
    receipt = own / "install.json"
    receipt.write_text(json.dumps({
        "schema": "copilot-extensions.plugin-installation", "version": 1,
        "marketplaceId": cell.name, "pluginId": own.name,
        "pluginRoot": str(own), "namespaceReceipt": str(namespace),
        "payload": {"root": str(payload), "version": "1.0.0", "origin": "explicit"},
        "roots": {name: name for name in (
            "versions", "snapshots", "state", "run", "logs", "cache", "launchers",
        )},
        "generation": 1, "state": "active",
    }), encoding="utf-8")
    environment, _ = installation._current_environment(
        environment=os.environ, os_profile=None, platform=None, wsl_distro=None,
    )
    activation = own / "installation-activation.json"
    activation.write_text(json.dumps({
        "schema": "copilot-extensions.installation-activation", "version": 1,
        "marketplaceId": cell.name, "pluginId": own.name, "mode": "namespaced",
        "state": "active", "environment": environment, "context": str(receipt),
        "namespaceGeneration": 1, "installGeneration": 1, "generation": 1,
        "createdAt": "2026-01-01T00:00:00Z", "updatedAt": "2026-01-01T00:00:00Z",
        "legacy": {"disposition": "absent", "probe": {
            "declared": True, "result": "absent", "checkedAt": "2026-01-01T00:00:00Z",
        }},
    }), encoding="utf-8")
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: own)
    monkeypatch.setattr(lifecycle.shutil, "which", lambda name: pytest.fail("cell used PATH"))
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda *a, **k: pytest.fail("health launched a child"))
    monkeypatch.setattr(health, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(
        health, "get_default_manager", lambda: SimpleNamespace(carrier_diagnostics=lambda: {}),
    )
    load = Mock(wraps=lifecycle._peer_launch._load_primitive)
    monkeypatch.setattr(lifecycle._peer_launch, "_load_primitive", load)
    app = FastAPI()
    app.include_router(health.router)
    with TestClient(app) as client:
        for _ in range(32):
            assert client.get("/health").json()["worktree_record_writer"] == {
                "installation_root": str(own.parent / "agent-worktrees"),
            }
        assert load.call_count == 1
        for path in (receipt, namespace, activation):
            original = path.read_text(encoding="utf-8")
            path.write_text("{}", encoding="utf-8")
            assert client.get("/health").json()["worktree_record_writer"] == {
                "installation_root": None,
            }
            path.write_text(original, encoding="utf-8")
            assert client.get("/health").json()["worktree_record_writer"] == {
                "installation_root": str(own.parent / "agent-worktrees"),
            }
        assert load.call_count == 1
