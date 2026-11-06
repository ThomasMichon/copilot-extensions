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
from agent_bridge import worktree_writer_observation as observation
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
        lifecycle, "_legacy_worktrees_runtime_prefix",
        lambda root: [str(peer / "versions" / "1.0.0" / "bin" / "python")]
        if selection == "legacy" else None,
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
        monkeypatch.setattr(lifecycle._peer_launch, "observe_peer_python", lambda *args: None)
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
            "runtime_generation": None,
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


@pytest.mark.parametrize("nested", [False, True])
def test_repeated_health_reuses_real_resolver_but_refreshes_selected_slot(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, nested: bool,
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

    def select(version: str, generation: str) -> Path:
        slot = peer / "versions" / version
        environment = slot / ".venv" if nested else slot
        python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        python.parent.mkdir(parents=True)
        python.write_bytes(b"selected interpreter fixture")
        (slot / ".install-complete.json").write_text(
            json.dumps({"version": version, "completed_at": "fixture", "pid": 0}),
            encoding="utf-8",
        )
        (peer / "current-version").write_text(version, encoding="utf-8")
        bootstrap = slot / ".runtime-bootstrap"
        bootstrap.mkdir()
        (bootstrap / "runtime-generation").write_text(generation + "\n", encoding="ascii")
        return python

    first = select("1.0.0", "a" * 32)
    (peer / "runtime-generation").write_text("c" * 32, encoding="ascii")
    app = FastAPI()
    app.include_router(health.router)
    with TestClient(app) as client:
        response = client.get("/health")
        assert response.status_code == 200
        assert response.json()["worktree_record_writer"] == {
            "installation_root": str(peer), "runtime_generation": "a" * 32,
        }
        initial = len(registrations)
        assert initial > 0 if register is not None else initial == 0
        assert lifecycle._legacy_worktrees_runtime_prefix(own)[0] == str(first)
        for _ in range(32):
            assert client.get("/health").json()["worktree_record_writer"] == {
                "installation_root": str(peer), "runtime_generation": "a" * 32,
            }
        second = select("2.0.0", "b" * 32)
        assert client.get("/health").json()["worktree_record_writer"] == {
            "installation_root": str(peer), "runtime_generation": "b" * 32,
        }
        assert lifecycle._legacy_worktrees_runtime_prefix(own)[0] == str(second)
        assert len(registrations) == initial


@pytest.mark.parametrize("case", [
    "legacy", "malformed", "oversized", "non-ascii", "mutable", "linked-generation",
    "linked-bootstrap", "linked-slot", "changed-selection", "changed-generation",
])
def test_unknown_or_changed_slot_never_certifies_a_build(tmp_path, monkeypatch, case):
    root = tmp_path / "worktrees"
    slot = root / "versions" / ("dev" if case == "mutable" else "1.0.0")
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"interpreter fixture")
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    generation = bootstrap / "runtime-generation"
    if case != "legacy":
        value = {"malformed": "unknown", "oversized": "a" * 65, "non-ascii": "\u2603"}
        generation.write_text(value.get(case, "a" * 32), encoding="utf-8")
    if case.startswith("linked-"):
        if os.name == "nt":
            pytest.skip("symlink control requires a native POSIX host")
        if case == "linked-generation":
            target = tmp_path / "foreign-generation"
            generation.rename(target)
            generation.symlink_to(target)
        elif case == "linked-bootstrap":
            target = tmp_path / "foreign-bootstrap"
            bootstrap.rename(target)
            bootstrap.symlink_to(target, target_is_directory=True)
        else:
            target = tmp_path / "foreign-slot"
            slot.rename(target)
            slot.symlink_to(target, target_is_directory=True)
    selected = (["bridge-owner"], root, python)
    calls = []

    def resolve(**kwargs):
        assert kwargs == {"allow_path_fallback": False}
        calls.append(None)
        if len(calls) == 2:
            if case == "changed-selection":
                return (["different-owner"], root, python)
            if case == "changed-generation":
                generation.write_text("b" * 32, encoding="ascii")
        return selected

    monkeypatch.setattr(lifecycle, "_agent_worktrees_runtime_selection", resolve)
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda *a, **k: pytest.fail("launched a child"))
    assert observation.observe() == {
        "installation_root": None if case.startswith("changed-") else str(root),
        "runtime_generation": None,
    }


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
                "installation_root": None,
                "runtime_generation": None,
            }
        assert load.call_count == 1
        for path in (receipt, namespace, activation):
            original = path.read_text(encoding="utf-8")
            path.write_text("{}", encoding="utf-8")
            assert client.get("/health").json()["worktree_record_writer"] == {
                "installation_root": None,
                "runtime_generation": None,
            }
            path.write_text(original, encoding="utf-8")
            assert client.get("/health").json()["worktree_record_writer"] == {
                "installation_root": None,
                "runtime_generation": None,
            }
        assert load.call_count == 1


@pytest.mark.parametrize("case", [
    "known", "native-only", "changed", "refused", "replaced-code",
    "runtime-error", "syntax-error", "late-runtime-error", "late-syntax-error",
    "validator-error", "late-validator-error",
])
def test_same_cell_health_rechecks_peer_selection_without_launch(tmp_path, monkeypatch, case):
    root = tmp_path / "marketplaces" / "fixture" / "plugins" / "agent-worktrees"
    slot = root / "versions" / "1.0.0"
    python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    python.parent.mkdir(parents=True)
    python.write_bytes(b"selected fixture")
    bootstrap = slot / ".runtime-bootstrap"
    bootstrap.mkdir()
    (bootstrap / "runtime-generation").write_text("a" * 32, encoding="ascii")
    monkeypatch.setattr(lifecycle, "_agent_worktrees_runtime_selection",
                        lambda **kw: (["native-owner"], root, None))
    monkeypatch.setattr(lifecycle, "_agent_bridge_installation_context", lambda: "receipt")
    monkeypatch.setattr(lifecycle, "_agent_bridge_owner_root", lambda: root.with_name("agent-bridge"))
    observed = []
    if "error" in case:
        scripts = tmp_path / "peer-payload" / "scripts"
        scripts.mkdir(parents=True)
        source = (
            "def broken(:\n" if "syntax" in case else
            "def resolve_python(root):\n raise RuntimeError('fixture failure')\n"
        )
        (scripts / "versioned_runtime.py").write_text(source, encoding="utf-8")
        monkeypatch.setattr(lifecycle._peer_launch, "_peer_runtimes", {})
        if "validator" in case:
            primitive = scripts / "installation-context"
            primitive.mkdir()
            provider = Path(__file__).resolve().parents[2] / "agent-worktrees" / "scripts" / "installation-context"
            shutil.copyfile(provider / "installation_context.py", primitive / "installation_context.py")
            (primitive / "_installation_context_base.py").write_text("def broken(:\n", encoding="utf-8")

    def select(*args):
        observed.append(args)
        if "error" in case and (not case.startswith("late-") or len(observed) == 2):
            if "validator" in case:
                return lifecycle._peer_launch._load_primitive(
                    primitive / "installation_context.py", "_broken_peer_validator_fixture",
                )
            return lifecycle._peer_launch._peer_python_inprocess({
                "payloadRoot": str(scripts.parent), "pluginRoot": str(root),
                "pluginId": "agent-worktrees", "versionsRoot": str(root / "versions"),
            })
        if case == "native-only":
            return None
        if case == "refused" or (case == "replaced-code" and len(observed) == 2):
            raise ValueError("peer evidence unavailable")
        if case == "changed" and len(observed) == 2:
            return python.with_name("different")
        return python

    monkeypatch.setattr(lifecycle._peer_launch, "observe_peer_python", select)
    monkeypatch.setattr(lifecycle.subprocess, "run", lambda *a, **k: pytest.fail("health launched"))
    monkeypatch.setattr(health, "config_dir", lambda: tmp_path)
    monkeypatch.setattr(health, "get_default_manager",
                        lambda: SimpleNamespace(carrier_diagnostics=lambda: {}))
    app = FastAPI()
    app.include_router(health.router)
    with TestClient(app) as client:
        result = client.get("/health")
    assert result.status_code == 200
    assert result.json()["worktree_record_writer"] == {
        "installation_root": str(root) if case in {"known", "native-only"} else None,
        "runtime_generation": "a" * 32 if case == "known" else None,
    }
