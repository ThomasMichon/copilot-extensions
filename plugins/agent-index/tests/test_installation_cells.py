from __future__ import annotations

import base64
import importlib.util
import json
import os
import shutil
import stat
import subprocess
import sys
import threading
import time
import urllib.request
import venv
from pathlib import Path
from types import SimpleNamespace

import pytest

from agent_index import config
from agent_index import __main__ as agent_main
from agent_index.index_config import IndexConfig


PLUGIN = Path(__file__).resolve().parents[1]
SCRIPT = PLUGIN / "scripts" / "cell-runtime.py"
SPEC = importlib.util.spec_from_file_location("agent_index_cell_runtime", SCRIPT)
assert SPEC is not None and SPEC.loader is not None
CELL = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(CELL)


def _roots(tmp_path: Path) -> dict[str, object]:
    plugin_root = tmp_path / "marketplaces" / "example" / "plugins" / "agent-index"
    return {
        "pluginRoot": str(plugin_root),
        "versionsRoot": str(plugin_root / "versions"),
        "snapshotsRoot": str(plugin_root / "snapshots"),
        "stateRoot": str(plugin_root / "state"),
        "runRoot": str(plugin_root / "run"),
        "logsRoot": str(plugin_root / "logs"),
        "cacheRoot": str(plugin_root / "cache"),
        "namespaceGeneration": 2,
        "generation": 3,
    }


def _write_runtime_slot_evidence(
    slot: Path,
    *,
    marketplace_id: str = "example--1234",
    runtime_version: str | None = None,
    role: str | None = None,
) -> None:
    runtime_version = runtime_version or slot.name
    slot.mkdir(parents=True, exist_ok=True)
    pyvenv = slot / "pyvenv.cfg"
    if not pyvenv.exists():
        pyvenv.write_text("home = synthetic\n", encoding="utf-8")
    (slot / ".runtime-slot-ownership.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.runtime-slot-ownership",
                "marketplaceId": marketplace_id,
                "pluginId": "agent-index",
                "runtime": {
                    "version": runtime_version,
                    "root": str(slot.resolve()),
                },
            }
        )
        + "\n",
        encoding="utf-8",
    )
    CELL._write_runtime_profile(
        slot,
        marketplace_id,
        runtime_version,
        role,
    )


def test_payload_manifest_requires_installation_context() -> None:
    manifest = json.loads(
        (PLUGIN / "payload-invocation.json").read_text(encoding="utf-8")
    )

    assert manifest["version"] == 2
    assert manifest["legacyRuntimeRoot"] == ".agent-index"
    assert manifest["installationContext"] == "required"
    assert manifest["payloadRootEnv"] == "AGENT_INDEX_PAYLOAD_ROOT"
    assert manifest["installer"] == "install"
    assert "runtimeRoot" not in manifest


def test_cell_environment_qualifies_every_mutable_root(tmp_path: Path) -> None:
    validated = _roots(tmp_path)
    context = Path(validated["pluginRoot"]) / "install.json"

    environment = CELL._cell_environment(validated, context, "example--1234")

    plugin_root = Path(validated["pluginRoot"])
    assert Path(environment["AGENT_INDEX_HOME"]) == plugin_root
    assert Path(environment["AGENT_INDEX_STATE_DIR"]) == plugin_root / "state"
    assert Path(environment["AGENT_INDEX_DATA_DIR"]) == plugin_root / "state"
    assert Path(environment["AGENT_INDEX_RUN_DIR"]) == plugin_root / "run"
    assert Path(environment["AGENT_INDEX_LOG_DIR"]) == plugin_root / "logs"
    assert Path(environment["AGENT_INDEX_CACHE_DIR"]) == plugin_root / "cache"
    assert Path(environment["AGENT_INDEX_CONFIG_ROOT"]) == plugin_root / "config"
    assert Path(environment["AGENT_INDEX_ENGINE_HOME"]) == plugin_root / "engine"
    assert Path(environment["AGENT_INDEX_BACKUP_DIR"]) == plugin_root / "backups"
    assert Path(environment["AGENT_INDEX_BACKUP_MOUNT_ROOT"]) == plugin_root
    assert environment["AGENT_INDEX_HOST"] == "127.0.0.1"
    assert environment["AGENT_INDEX_PORT"] == "0"
    assert environment["AGENT_INDEX_ENGINE_PORT"] == "0"
    assert environment["AGENT_INDEX_ENGINE_MODE"] == "external"
    assert Path(environment["AGENT_INDEX_ROUTING_DIR"]) == plugin_root / "run" / "zdd"
    assert environment["AGENT_INDEX_INSTALLATION_ID"] == "example--1234/agent-index"


def test_runtime_config_honors_cell_local_state_and_routing(
    monkeypatch, tmp_path: Path
) -> None:
    monkeypatch.setenv("AGENT_INDEX_HOME", str(tmp_path / "plugin"))
    monkeypatch.delenv("AGENT_INDEX_DATA_DIR", raising=False)
    monkeypatch.setenv("AGENT_INDEX_STATE_DIR", str(tmp_path / "state"))
    monkeypatch.setenv("AGENT_INDEX_RUN_DIR", str(tmp_path / "run"))
    monkeypatch.setenv("AGENT_INDEX_CONFIG_ROOT", str(tmp_path / "config"))
    monkeypatch.setenv("AGENT_INDEX_ROUTING_DIR", str(tmp_path / "run" / "zdd"))

    assert config.install_dir() == tmp_path / "plugin"
    assert config.data_dir() == tmp_path / "state"
    assert config.run_dir() == tmp_path / "run"
    assert config.config_path() == tmp_path / "config" / "config.yaml"
    assert config.routing_dir() == tmp_path / "run" / "zdd"


def test_backup_paths_are_qualified_by_cell_home(monkeypatch, tmp_path: Path) -> None:
    paths = []
    for name in ("one", "two"):
        root = tmp_path / name
        monkeypatch.setenv("AGENT_INDEX_HOME", str(root))
        monkeypatch.setenv("AGENT_INDEX_DATA_DIR", str(root / "state"))
        monkeypatch.delenv("AGENT_INDEX_BACKUP_DIR", raising=False)
        monkeypatch.delenv("AGENT_INDEX_BACKUP_MOUNT_ROOT", raising=False)
        cfg = IndexConfig()
        paths.append(
            (
                cfg.data_dir / "backup-status.json",
                cfg.backup_state_dir / "backup-status.json",
                cfg.backup_snapshots_dir / "agent-index-2026-01-01.tar.zst",
            )
        )

    assert set(paths[0]).isdisjoint(paths[1])


def test_schema_four_manifest_preserves_source_on_historical_cutover(
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    context = plugin_root / "install.json"
    source = tmp_path / "payload-current"
    historical = tmp_path / "payload-historical"
    source.mkdir()
    historical.mkdir()
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")

    current_slot = plugin_root / "versions" / "2.0.0"
    current_python = CELL._venv_python(current_slot)
    current_python.parent.mkdir(parents=True)
    current_python.write_text("", encoding="utf-8")
    (plugin_root / "current-version").write_text("2.0.0\n", encoding="utf-8")
    first = CELL._write_manifest(
        plugin_root,
        context,
        "example--1234",
        source,
        "2.0.0",
        source,
        "2.0.0",
        "2.0.0",
        "2.0.0",
        preserve_source=False,
    )

    rollback_slot = plugin_root / "versions" / "1.0.0"
    rollback_python = CELL._venv_python(rollback_slot)
    rollback_python.parent.mkdir(parents=True)
    rollback_python.write_text("", encoding="utf-8")
    (plugin_root / "current-version").write_text("1.0.0\n", encoding="utf-8")
    rolled_back = CELL._write_manifest(
        plugin_root,
        context,
        "example--1234",
        historical,
        "1.0.0",
        historical,
        "1.0.0",
        "1.0.0",
        "1.0.0",
        preserve_source=True,
    )

    assert first["schema_version"] == 4
    assert rolled_back["source"] == first["source"]
    assert rolled_back["runtime"]["version"] == "1.0.0"
    assert rolled_back["runtime"]["selectedBy"]["version"] == "1.0.0"
    assert rolled_back["runtime"]["selectedBy"]["snapshotId"] == "1.0.0"
    assert rolled_back["runtime"]["selectedBy"]["path"] == historical.resolve().as_posix()


def _selection_fixture(
    tmp_path: Path,
    *,
    prior_version: str,
    target_version: str,
) -> tuple[
    dict[str, object],
    Path,
    Path,
    Path,
    dict[str, object],
    dict[str, object],
]:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    prior_payload = tmp_path / f"payload-{prior_version}"
    target_payload = tmp_path / f"payload-{target_version}"
    prior_payload.mkdir()
    target_payload.mkdir()
    for version in (prior_version, target_version):
        interpreter = CELL._venv_python(plugin_root / "versions" / version)
        interpreter.parent.mkdir(parents=True)
        interpreter.write_text("", encoding="utf-8")
    (plugin_root / "current-version").write_text(
        prior_version + "\n",
        encoding="utf-8",
    )
    (plugin_root / "last-known-good").write_text(
        prior_version + "\n",
        encoding="utf-8",
    )
    prior_manifest = CELL._write_manifest(
        plugin_root,
        context,
        "example--1234",
        PLUGIN,
        CELL._plugin_version(PLUGIN),
        prior_payload,
        prior_version,
        prior_version,
        prior_version,
        preserve_source=False,
    )
    transaction = CELL._prepare_selection_transaction(
        plugin_root,
        context,
        "example--1234",
        PLUGIN,
        CELL._plugin_version(PLUGIN),
        target_payload,
        target_version,
        target_version,
        target_version,
        str(validated["namespaceGeneration"]),
        str(validated["generation"]),
        validated,
        preserve_source=True,
    )
    return (
        validated,
        plugin_root,
        context,
        target_payload,
        prior_manifest,
        transaction,
    )


def _patch_selection_runtime(
    monkeypatch,
    plugin_root: Path,
    *,
    governance: list[tuple[bool, dict[str, object]]] | None = None,
    reconciled: list[str] | None = None,
) -> None:
    monkeypatch.setattr(
        CELL,
        "_validate_transaction_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        CELL,
        "_validate_recorded_selection",
        lambda *_args, **_kwargs: plugin_root / "python",
    )
    states = list(
        governance
        or [
            (True, {"status": "ready"}),
            (True, {"status": "ready"}),
            (True, {"status": "ready"}),
        ]
    )

    def next_governance(*_args):
        if len(states) > 1:
            return states.pop(0)
        return states[0]

    monkeypatch.setattr(CELL, "_selection_governance", next_governance)

    def slot_cutover(
        _management,
        _target_payload,
        _context,
        _marketplace,
        _durable,
        _payload_version,
        _snapshot,
        runtime_version,
        _namespace_generation,
        _install_generation,
        expected_current,
    ):
        assert CELL._marker_version(plugin_root) == expected_current
        for name in ("current-version", "last-known-good"):
            (plugin_root / name).write_text(
                str(runtime_version) + "\n",
                encoding="utf-8",
            )
        return {"status": "ready"}

    monkeypatch.setattr(CELL, "_slot_cutover", slot_cutover)
    monkeypatch.setattr(
        CELL,
        "_selected_runtime",
        lambda *_args, **_kwargs: ({}, plugin_root / "python"),
    )
    monkeypatch.setattr(
        CELL,
        "_write_launchers",
        lambda *_args, **_kwargs: (
            plugin_root / "service",
            plugin_root / "command",
        ),
    )
    monkeypatch.setattr(
        CELL,
        "_reconcile_service",
        lambda _validated, _service, _command, version, _env, _token: (
            (reconciled.append(version) if reconciled is not None else None)
            or {"version": version}
        ),
    )


@pytest.mark.parametrize(
    "failure_phase",
    ["after-marker", "before-manifest", "after-manifest"],
)
def test_selection_transaction_recovers_marker_manifest_interruptions(
    monkeypatch,
    tmp_path: Path,
    failure_phase: str,
) -> None:
    validated, plugin_root, context, _target, _prior, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    reconciled: list[str] = []
    _patch_selection_runtime(
        monkeypatch,
        plugin_root,
        reconciled=reconciled,
    )
    monkeypatch.setenv("AGENT_INDEX_TEST_SELECTION_FAILURE", failure_phase)

    with pytest.raises(CELL.CellError, match="injected selection failure"):
        CELL._resume_selection_transaction(
            PLUGIN,
            context,
            "example--1234",
            tmp_path,
            validated,
            "lock-token",
            transaction,
        )

    assert CELL._marker_version(plugin_root) == "2.0.0"
    assert (plugin_root / CELL.TRANSACTION_FILE).is_file()
    monkeypatch.delenv("AGENT_INDEX_TEST_SELECTION_FAILURE")
    _patch_selection_runtime(
        monkeypatch,
        plugin_root,
        reconciled=reconciled,
    )
    pending = CELL._load_selection_transaction(
        plugin_root,
        context,
        "example--1234",
    )
    assert pending is not None

    result = CELL._resume_selection_transaction(
        PLUGIN,
        context,
        "example--1234",
        tmp_path,
        validated,
        "lock-token",
        pending,
    )

    manifest = CELL._load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        "example--1234",
    )
    assert result["status"] == "ready"
    assert manifest is not None
    assert manifest["runtime"]["version"] == "2.0.0"
    assert reconciled == ["2.0.0"]
    assert not (plugin_root / CELL.TRANSACTION_FILE).exists()
    receipt = json.loads(
        (plugin_root / CELL.TRANSACTION_RECEIPT_FILE).read_text(encoding="utf-8")
    )
    assert receipt["outcome"] == "committed"


def test_interrupted_historical_rollback_is_idempotently_recovered(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated, plugin_root, context, _target, prior_manifest, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="2.0.0",
            target_version="1.0.0",
        )
    )
    _patch_selection_runtime(monkeypatch, plugin_root)
    monkeypatch.setenv("AGENT_INDEX_TEST_SELECTION_FAILURE", "after-marker")
    with pytest.raises(CELL.CellError):
        CELL._resume_selection_transaction(
            PLUGIN,
            context,
            "example--1234",
            tmp_path,
            validated,
            "lock-token",
            transaction,
        )
    monkeypatch.delenv("AGENT_INDEX_TEST_SELECTION_FAILURE")
    _patch_selection_runtime(monkeypatch, plugin_root)
    pending = CELL._load_selection_transaction(
        plugin_root,
        context,
        "example--1234",
    )
    assert pending is not None

    CELL._resume_selection_transaction(
        PLUGIN,
        context,
        "example--1234",
        tmp_path,
        validated,
        "lock-token",
        pending,
    )

    manifest = CELL._load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        "example--1234",
    )
    assert manifest is not None
    assert manifest["source"] == prior_manifest["source"]
    assert manifest["runtime"]["version"] == "1.0.0"
    assert CELL._marker_version(plugin_root) == "1.0.0"


def test_pending_transaction_resumes_from_current_upgraded_management_payload(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated, plugin_root, context, _target, _prior, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    current_management = tmp_path / "moved-management"
    current_management.mkdir()
    (current_management / "pyproject.toml").write_text(
        '[project]\nname = "agent-index"\nversion = "3.0.0"\n',
        encoding="utf-8",
    )
    advanced = dict(validated)
    advanced["namespaceGeneration"] = 8
    advanced["generation"] = 9
    _patch_selection_runtime(monkeypatch, plugin_root)
    calls: list[tuple[str, str]] = []

    def cutover(
        _management,
        _target_payload,
        _context,
        _marketplace,
        _durable,
        _payload_version,
        _snapshot,
        runtime_version,
        namespace_generation,
        install_generation,
        expected_current,
    ):
        calls.append((namespace_generation, install_generation))
        assert expected_current == "1.0.0"
        for name in ("current-version", "last-known-good"):
            (plugin_root / name).write_text(
                runtime_version + "\n",
                encoding="utf-8",
            )
        return {"status": "ready"}

    monkeypatch.setattr(CELL, "_slot_cutover", cutover)

    result = CELL._resume_selection_transaction(
        current_management,
        context,
        "example--1234",
        tmp_path,
        advanced,
        "lock-token",
        transaction,
    )

    manifest = CELL._load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        "example--1234",
    )
    assert result["status"] == "ready"
    assert calls == [("8", "9")]
    assert manifest is not None
    assert manifest["source"]["path"] == current_management.resolve().as_posix()
    assert manifest["source"]["version"] == "3.0.0"
    assert manifest["runtime"]["version"] == "2.0.0"
    assert not (plugin_root / CELL.TRANSACTION_FILE).exists()


def test_pending_transaction_revalidates_advanced_generation_without_wedging(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated, plugin_root, context, _target, _prior, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    _patch_selection_runtime(monkeypatch, plugin_root)
    calls: list[tuple[str, str]] = []

    def cutover(
        _management,
        _target_payload,
        _context,
        _marketplace,
        _durable,
        _payload_version,
        _snapshot,
        runtime_version,
        namespace_generation,
        install_generation,
        expected_current,
    ):
        calls.append((namespace_generation, install_generation))
        if len(calls) == 1:
            return {
                "status": "revalidation-required",
                "reason": "generation-changed",
                "namespaceGeneration": 12,
                "installGeneration": 13,
                "currentVersion": expected_current,
            }
        for name in ("current-version", "last-known-good"):
            (plugin_root / name).write_text(
                runtime_version + "\n",
                encoding="utf-8",
            )
        return {"status": "ready"}

    monkeypatch.setattr(CELL, "_slot_cutover", cutover)

    result = CELL._resume_selection_transaction(
        PLUGIN,
        context,
        "example--1234",
        tmp_path,
        validated,
        "lock-token",
        transaction,
    )

    assert result["status"] == "ready"
    assert calls == [("2", "3"), ("12", "13")]
    assert not (plugin_root / CELL.TRANSACTION_FILE).exists()


@pytest.mark.parametrize(
    "failure",
    [
        "new daemon did not become healthy within the health timeout",
        "old daemon did not drain because active reads refused retirement",
    ],
    ids=["health-timeout", "drain-refusal"],
)
def test_service_reconciliation_failure_restores_prior_runtime_and_artifacts(
    monkeypatch,
    tmp_path: Path,
    failure: str,
) -> None:
    selected_runtime = CELL._selected_runtime
    validated, plugin_root, context, _target, _prior, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    prior_artifacts: dict[Path, bytes] = {}
    for index, path in enumerate(CELL._transaction_artifact_paths(plugin_root)):
        path.parent.mkdir(parents=True, exist_ok=True)
        content = f"prior-{index}\n".encode()
        path.write_bytes(content)
        prior_artifacts[path] = content
    transaction = dict(transaction)
    transaction["prior"] = dict(transaction["prior"])
    transaction["prior"]["artifacts"] = CELL._capture_transaction_artifacts(
        plugin_root
    )
    transaction = CELL._write_selection_transaction(plugin_root, transaction)
    _patch_selection_runtime(monkeypatch, plugin_root)
    interpreter = plugin_root / "python"
    interpreter.write_text("", encoding="utf-8")

    def write_target_launchers(*_args, **_kwargs):
        paths = CELL._transaction_artifact_paths(plugin_root)
        for index, path in enumerate(paths):
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_bytes(f"target-{index}\n".encode())
        return paths[0], paths[1]

    monkeypatch.setattr(CELL, "_write_launchers", write_target_launchers)
    monkeypatch.setattr(
        CELL,
        "_reconcile_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(CELL.CellError(failure)),
    )

    with pytest.raises(CELL.CellError, match=failure):
        CELL._resume_selection_transaction(
            PLUGIN,
            context,
            "example--1234",
            tmp_path,
            validated,
            "lock-token",
            transaction,
        )

    assert CELL._marker_version(plugin_root) == "1.0.0"
    manifest = CELL._load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        "example--1234",
    )
    assert manifest is not None
    assert manifest["runtime"]["version"] == "1.0.0"
    assert all(path.read_bytes() == content for path, content in prior_artifacts.items())
    assert not (plugin_root / CELL.TRANSACTION_FILE).exists()
    receipt = json.loads(
        (plugin_root / CELL.TRANSACTION_RECEIPT_FILE).read_text(encoding="utf-8")
    )
    assert receipt["outcome"] == "restored-service-reconciliation-failure"
    selected, selected_interpreter = selected_runtime(
        PLUGIN,
        validated,
        context,
        "example--1234",
        tmp_path,
    )
    assert selected is not None
    assert selected["runtime"]["version"] == "1.0.0"
    assert selected_interpreter == interpreter


def test_launch_validate_blocks_pending_target_selection(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated, plugin_root, context, _target, _prior, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    for name in ("current-version", "last-known-good"):
        (plugin_root / name).write_text("2.0.0\n", encoding="utf-8")
    CELL._atomic_json(
        plugin_root / "deploy-manifest.json",
        transaction["target"]["manifest"],
    )
    monkeypatch.setattr(CELL, "_validate_context", lambda *_args, **_kwargs: validated)
    monkeypatch.setattr(
        CELL,
        "_installation_status",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
        },
    )
    monkeypatch.setattr(CELL, "_governance_mode", lambda *_args, **_kwargs: "namespaced")
    monkeypatch.setattr(
        CELL,
        "_selected_runtime",
        lambda *_args, **_kwargs: pytest.fail("pending target was exposed"),
    )

    with pytest.raises(CELL.CellError, match="dispatch is blocked"):
        CELL.launch_validate(
            SimpleNamespace(
                context=str(context),
                durable_home=str(tmp_path),
                expected_marketplace_id="example--1234",
                command="search",
            ),
            PLUGIN,
        )


def test_pending_transaction_can_commit_valid_target_when_prior_slot_is_unusable(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated, plugin_root, context, _target, _prior, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    _patch_selection_runtime(monkeypatch, plugin_root)
    monkeypatch.setattr(
        CELL,
        "_validate_recorded_selection",
        lambda *_args, label, **_kwargs: (
            (_ for _ in ()).throw(CELL.CellError("prior runtime is unusable"))
            if label == "prior"
            else plugin_root / "python"
        ),
    )

    result = CELL._resume_selection_transaction(
        PLUGIN,
        context,
        "example--1234",
        tmp_path,
        validated,
        "lock-token",
        transaction,
    )

    assert result["status"] == "ready"
    assert CELL._marker_version(plugin_root) == "2.0.0"
    assert not (plugin_root / CELL.TRANSACTION_FILE).exists()


def test_governance_restore_uses_current_management_source_provenance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated, plugin_root, context, _target, _prior, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    current_management = tmp_path / "management-current"
    current_management.mkdir()
    (current_management / "pyproject.toml").write_text(
        '[project]\nname = "agent-index"\nversion = "3.0.0"\n',
        encoding="utf-8",
    )
    _patch_selection_runtime(
        monkeypatch,
        plugin_root,
        governance=[
            (
                False,
                {
                    "status": "maintenance",
                    "reason": "maintenance",
                    "actualMode": "namespaced",
                },
            )
        ],
    )

    result = CELL._resume_selection_transaction(
        current_management,
        context,
        "example--1234",
        tmp_path,
        validated,
        "lock-token",
        transaction,
    )

    manifest = CELL._load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        "example--1234",
    )
    assert result["status"] == "governance-blocked"
    assert manifest is not None
    assert manifest["source"]["path"] == current_management.resolve().as_posix()
    assert manifest["source"]["version"] == "3.0.0"
    assert manifest["runtime"]["version"] == "1.0.0"
    assert not (plugin_root / CELL.TRANSACTION_FILE).exists()


def test_governance_rechecked_before_marker_and_service(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated, plugin_root, context, _target, prior_manifest, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    reconciled: list[str] = []
    _patch_selection_runtime(
        monkeypatch,
        plugin_root,
        governance=[
            (True, {"status": "ready"}),
            (
                False,
                {
                    "status": "maintenance",
                    "reason": "maintenance",
                    "actualMode": "namespaced",
                },
            ),
        ],
        reconciled=reconciled,
    )

    result = CELL._resume_selection_transaction(
        PLUGIN,
        context,
        "example--1234",
        tmp_path,
        validated,
        "lock-token",
        transaction,
    )

    manifest = CELL._load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        "example--1234",
    )
    assert result["status"] == "governance-blocked"
    assert CELL._marker_version(plugin_root) == "1.0.0"
    assert manifest == prior_manifest
    assert reconciled == []
    assert not (plugin_root / CELL.TRANSACTION_FILE).exists()


@pytest.mark.parametrize(
    "blocked",
    [
        {
            "status": "maintenance",
            "reason": "maintenance",
            "actualMode": "namespaced",
        },
        {
            "status": "deactivation-required",
            "reason": "deactivation-required",
            "actualMode": "namespaced",
        },
    ],
)
def test_governance_block_during_passive_prepare_restores_selection_untouched(
    monkeypatch,
    tmp_path: Path,
    blocked: dict[str, object],
) -> None:
    validated, plugin_root, context, _target, prior_manifest, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    artifact_paths = CELL._transaction_artifact_paths(plugin_root)
    for index, path in enumerate(artifact_paths):
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(f"prior-{index}".encode())
    transaction["prior"]["artifacts"] = CELL._capture_transaction_artifacts(
        plugin_root
    )
    installation_id = "example--1234/agent-index"
    prior_record = {
        "schema": CELL.INSTANCE_SCHEMA,
        "version": 1,
        "installationId": installation_id,
        "runtimeVersion": "1.0.0",
        "pid": 101,
        "instanceToken": "prior-token",
        "host": "127.0.0.1",
        "port": 4101,
        "state": "active",
        "transactionId": None,
    }
    target_record = {
        "schema": CELL.INSTANCE_SCHEMA,
        "version": 1,
        "installationId": installation_id,
        "runtimeVersion": "2.0.0",
        "pid": 202,
        "instanceToken": "target-token",
        "host": "127.0.0.1",
        "port": 4202,
        "state": "passive",
        "transactionId": transaction["id"],
    }
    transaction["prior"]["instances"] = [prior_record]
    transaction["prior"]["activeService"] = {
        **prior_record,
        "draining": False,
    }
    CELL._atomic_json(plugin_root / CELL.TRANSACTION_FILE, transaction)
    instances = Path(validated["runRoot"]) / "instances"
    instances.mkdir(parents=True)
    CELL._atomic_json(instances / "101.json", prior_record)
    live = {101}
    stopped: list[int] = []
    published: list[int] = []
    _patch_selection_runtime(
        monkeypatch,
        plugin_root,
        governance=[
            (True, {"status": "ready"}),
            (True, {"status": "ready"}),
        ],
    )

    def write_launchers(*_args, **_kwargs):
        for index, path in enumerate(artifact_paths):
            path.write_bytes(f"target-{index}".encode())
        return artifact_paths[0], artifact_paths[1]

    def reconcile(*_args, **_kwargs):
        CELL._atomic_json(instances / "202.json", target_record)
        raise CELL.CellGovernanceBlocked(
            "governance blocked before service commit",
            blocked,
        )

    def status(port: int):
        if port == 4101 and 101 in live:
            return {
                "status": "ok",
                "installationId": installation_id,
                "version": "1.0.0",
                "pid": 101,
                "instanceToken": "prior-token",
            }
        return None

    class Routing:
        @staticmethod
        def publish_active(_root, **kwargs):
            published.append(int(kwargs["pid"]))

    monkeypatch.setattr(CELL, "_write_launchers", write_launchers)
    monkeypatch.setattr(CELL, "_reconcile_service", reconcile)
    monkeypatch.setattr(CELL, "_service_status", status)
    monkeypatch.setattr(CELL, "_pid_alive", lambda pid: pid in live)
    monkeypatch.setattr(
        CELL,
        "_active_service",
        lambda *_args: {
            "port": 4101,
            "pid": 101,
            "version": "1.0.0",
            "installationId": installation_id,
            "instanceToken": "prior-token",
            "draining": False,
        },
    )
    monkeypatch.setattr(CELL, "_payload_routing_module", lambda: Routing())
    monkeypatch.setattr(
        CELL,
        "_undrain_owned_instance",
        lambda active, _installation: {**active, "draining": False},
    )
    monkeypatch.setattr(
        CELL,
        "_shutdown_owned_instance",
        lambda record, _installation: (
            stopped.append(int(record["pid"])),
            live.discard(int(record["pid"])),
        ),
    )
    monkeypatch.setattr(
        CELL,
        "_clear_owned_service_evidence",
        lambda *_args, **_kwargs: None,
    )

    result = CELL._resume_selection_transaction(
        PLUGIN,
        context,
        "example--1234",
        tmp_path,
        validated,
        "lock-token",
        transaction,
    )

    assert result["status"] == "governance-blocked"
    assert CELL._marker_version(plugin_root) == "1.0.0"
    assert CELL._load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        "example--1234",
    ) == prior_manifest
    assert [path.read_bytes() for path in artifact_paths] == [
        b"prior-0",
        b"prior-1",
        b"prior-2",
    ]
    assert published == []
    assert stopped == []
    assert live == {101}
    assert not (instances / "202.json").exists()
    assert not (plugin_root / CELL.TRANSACTION_FILE).exists()
    receipt = json.loads(
        (plugin_root / CELL.TRANSACTION_RECEIPT_FILE).read_text(encoding="utf-8")
    )
    assert receipt["outcome"] == "governance-blocked-before-commit"


def test_governance_block_before_marker_keeps_completed_target_inert(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated, plugin_root, context, _target, prior_manifest, transaction = (
        _selection_fixture(
            tmp_path,
            prior_version="1.0.0",
            target_version="2.0.0",
        )
    )
    monkeypatch.setattr(
        CELL,
        "_validate_transaction_target",
        lambda *_args, **_kwargs: None,
    )
    monkeypatch.setattr(
        CELL,
        "_validate_recorded_selection",
        lambda *_args, **_kwargs: plugin_root / "python",
    )
    monkeypatch.setattr(
        CELL,
        "_selection_governance",
        lambda *_args: (
            False,
            {
                "status": "blocked",
                "reason": "namespace-blocked",
                "actualMode": "namespaced",
            },
        ),
    )
    monkeypatch.setattr(
        CELL,
        "_slot_cutover",
        lambda *_args, **_kwargs: pytest.fail("blocked target changed selection"),
    )

    result = CELL._resume_selection_transaction(
        PLUGIN,
        context,
        "example--1234",
        tmp_path,
        validated,
        "lock-token",
        transaction,
    )

    manifest = CELL._load_manifest(
        plugin_root / "deploy-manifest.json",
        plugin_root,
        context,
        "example--1234",
    )
    assert result["status"] == "governance-blocked"
    assert CELL._marker_version(plugin_root) == "1.0.0"
    assert manifest == prior_manifest
    assert (plugin_root / "versions" / "2.0.0").is_dir()


def test_cell_launchers_are_installation_local_and_context_validating(
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    context = plugin_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    runtime = CELL._venv_python(plugin_root / "versions" / "2.0.0")
    runtime.parent.mkdir(parents=True)
    runtime.write_text("", encoding="utf-8")
    (plugin_root / "current-version").write_text("2.0.0\n", encoding="utf-8")

    launcher, command_launcher = CELL._write_launchers(
        validated,
        context,
        "example--1234",
        PLUGIN,
        CELL._plugin_version(PLUGIN),
        "2.0.0",
    )

    assert launcher.parent == plugin_root / "launchers"
    text = launcher.read_text(encoding="utf-8")
    assert str(context) in text
    assert str(PLUGIN / "scripts" / "runtime-gate") in text
    assert str(runtime) not in text
    assert ".local" not in text
    assert "systemctl" not in text
    assert "ScheduledTask" not in text
    assert "__cell-start" in text
    assert "PYTHONPATH" in text
    assert "AGENT_INDEX_RUNTIME_VERSION" in text
    assert "2.0.0" in text
    identity = json.loads(
        (plugin_root / "run" / "service-identity.json").read_text(encoding="utf-8")
    )
    assert identity["marketplaceId"] == "example--1234"
    assert identity["runtimeVersion"] == "2.0.0"
    assert identity["managementPayloadRoot"] == PLUGIN.resolve().as_posix()
    assert command_launcher.is_file()


def test_parent_lock_reenters_through_generated_launcher_for_recovery(
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    payload = tmp_path / "payload"
    scripts = payload / "scripts"
    context_scripts = scripts / "installation-context"
    context_scripts.mkdir(parents=True)
    (payload / "pyproject.toml").write_text(
        '[project]\nname = "agent-index"\nversion = "9.9.9"\n',
        encoding="utf-8",
    )
    if os.name == "nt":
        shutil.copy2(
            PLUGIN / "scripts" / "runtime-gate.ps1",
            scripts / "runtime-gate.ps1",
        )
        shutil.copy2(
            PLUGIN / "scripts" / "resolve_effective_config.py",
            scripts / "resolve_effective_config.py",
        )
        (scripts / "resolve-runtime.ps1").write_text(
            "$AgentRtPy = $null\n",
            encoding="utf-8",
        )
        (context_scripts / "installation-context.ps1").write_text(
            "$ErrorActionPreference = 'Stop'\n"
            "if ($args[0] -eq 'status') {\n"
            "  Write-Output $env:TEST_INSTALLATION_STATUS\n"
            "  exit 0\n"
            "}\n"
            "$root = $env:TEST_CELL_ROOT\n"
            "[ordered]@{\n"
            "  pluginRoot = $root\n"
            "  versionsRoot = Join-Path $root 'versions'\n"
            "  snapshotsRoot = Join-Path $root 'snapshots'\n"
            "  stateRoot = Join-Path $root 'state'\n"
            "  runRoot = Join-Path $root 'run'\n"
            "  logsRoot = Join-Path $root 'logs'\n"
            "  cacheRoot = Join-Path $root 'cache'\n"
            "  namespaceGeneration = 2\n"
            "  generation = 3\n"
            "} | ConvertTo-Json -Compress\n",
            encoding="utf-8",
        )
    else:
        shutil.copy2(
            PLUGIN / "scripts" / "runtime-gate.sh",
            scripts / "runtime-gate.sh",
        )
        shutil.copy2(
            PLUGIN / "scripts" / "resolve_effective_config.py",
            scripts / "resolve_effective_config.py",
        )
        shutil.copy2(
            PLUGIN / "scripts" / "installation-context" / "json-query.awk",
            context_scripts / "json-query.awk",
        )
        (scripts / "resolve-runtime.sh").write_text(
            'AGENT_RT_PY=""\n',
            encoding="utf-8",
        )
        (context_scripts / "installation-context.sh").write_text(
            "#!/usr/bin/env bash\n"
            "set -eu\n"
            'if [ "$1" = status ]; then\n'
            "  printf '%s\\n' \"$TEST_INSTALLATION_STATUS\"\n"
            "  exit 0\n"
            "fi\n"
            'root="${TEST_CELL_ROOT:?}"\n'
            "printf '{\"pluginRoot\":\"%s\",\"versionsRoot\":\"%s/versions\","
            "\"snapshotsRoot\":\"%s/snapshots\",\"stateRoot\":\"%s/state\","
            "\"runRoot\":\"%s/run\",\"logsRoot\":\"%s/logs\","
            "\"cacheRoot\":\"%s/cache\",\"namespaceGeneration\":2,"
            "\"generation\":3}\\n' "
            '"$root" "$root" "$root" "$root" "$root" "$root" "$root"\n',
            encoding="utf-8",
            newline="\n",
        )

    runtime_version = "9.9.9+host"
    slot = plugin_root / "versions" / runtime_version
    interpreter = CELL._venv_python(slot)
    venv.EnvBuilder(with_pip=False).create(slot)
    site_result = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-X",
            "utf8",
            "-c",
            (
                "import site; print(next(p for p in site.getsitepackages() "
                "if p.endswith(('site-packages','dist-packages'))))"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    package = Path(site_result.stdout.strip()) / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    (package / "__main__.py").write_text(
        "import json, os, sys\n"
        "from pathlib import Path\n"
        "keys = (\n"
        "    'AGENT_INDEX_CELL_LOCK_TOKEN',\n"
        "    'AGENT_INDEX_CELL_LOCK_ROOT',\n"
        "    'AGENT_INDEX_CELL_START_TOKEN',\n"
        "    'AGENT_INDEX_CELL_TRANSACTION',\n"
        "    'AGENT_INDEX_CELL_TRANSACTION_TOKEN',\n"
        "    'AGENT_INDEX_CELL_TRANSACTION_ID',\n"
        ")\n"
        "Path(os.environ['TEST_CAPTURE']).write_text(\n"
        "    json.dumps({'argv': sys.argv[1:], "
        "'environment': {key: os.environ.get(key) for key in keys}}),\n"
        "    encoding='utf-8',\n"
        ")\n"
        "print(json.dumps({'ok': True}))\n",
        encoding="utf-8",
    )
    _write_runtime_slot_evidence(
        slot,
        runtime_version=runtime_version,
        role="host",
    )

    wrapper = (
        "import importlib.util\n"
        "from pathlib import Path\n"
        f"_path = Path({str(SCRIPT)!r})\n"
        "_spec = importlib.util.spec_from_file_location('cell_runtime_real', _path)\n"
        "_module = importlib.util.module_from_spec(_spec)\n"
        "_spec.loader.exec_module(_module)\n"
        "_module.__file__ = str(Path(__file__).resolve())\n"
        "_module.LOCK_TIMEOUT_SECONDS = 0.2\n"
        f"_validated = {validated!r}\n"
        f"_interpreter = Path({str(interpreter)!r})\n"
        "_module._validate_context = lambda *_args, **_kwargs: _validated\n"
        "_module._installation_status = lambda *_args, **_kwargs: {\n"
        "    'status': 'ready',\n"
        "    'reason': 'namespaced-active',\n"
        "    'actualMode': 'namespaced',\n"
        "}\n"
        "_module._governance_mode = lambda *_args, **_kwargs: 'namespaced'\n"
        "_module._selected_runtime = lambda *_args, **_kwargs: (\n"
        f"    {{'runtime': {{'version': {runtime_version!r}}}}}, _interpreter\n"
        ")\n"
        "_module._validate_launcher_artifacts = lambda *_args, **_kwargs: None\n"
        "raise SystemExit(_module.main())\n"
    )
    (scripts / "cell-runtime.py").write_text(wrapper, encoding="utf-8")
    capture = tmp_path / "runtime-environment.json"
    service_launcher, command_launcher = CELL._write_launchers(
        validated,
        context,
        "example--1234",
        payload,
        "9.9.9",
        runtime_version,
    )
    transaction = CELL._prepare_selection_transaction(
        plugin_root,
        context,
        "example--1234",
        payload,
        "9.9.9",
        payload,
        "9.9.9",
        "9.9.9",
        runtime_version,
        str(validated["namespaceGeneration"]),
        str(validated["generation"]),
        validated,
        preserve_source=False,
    )
    transaction = CELL._write_selection_transaction(
        plugin_root,
        transaction,
        state="reconciling",
    )
    (plugin_root / "current-version").write_text(
        runtime_version + "\n",
        encoding="utf-8",
    )
    profile = tmp_path / "profile"
    profile.mkdir()
    inline_config = base64.urlsafe_b64encode(
        json.dumps({"indexer": {"machine": "test-host"}}).encode("utf-8")
    ).decode("ascii")
    environment = os.environ.copy()
    environment.update(
        CELL._transaction_environment(
            CELL._cell_environment(
                validated,
                context,
                "example--1234",
                runtime_version,
            ),
            plugin_root,
            transaction,
        )
    )
    environment.update(
        {
            "HOME": str(profile),
            "USERPROFILE": str(profile),
            "AGENT_INDEX_ROLE": "host",
            "AGENT_INDEX_CONFIG_DATA_B64": inline_config,
            "TEST_CAPTURE": str(capture),
            "TEST_CELL_ROOT": str(plugin_root),
            "TEST_INSTALLATION_STATUS": json.dumps(
                {
                    "status": "ready",
                    "reason": "namespaced-active",
                    "actualMode": "namespaced",
                    "desiredMode": "namespaced",
                    "context": str(context),
                    "marketplaceId": "example--1234",
                    "policy": {"state": "valid", "enabled": True},
                }
            ),
        }
    )
    if os.name != "nt":
        fake_bin = tmp_path / "bin"
        fake_bin.mkdir()
        getent = fake_bin / "getent"
        getent.write_text(
            "#!/bin/sh\n"
            'printf "tester:x:%s:%s::%s:/bin/sh\\n" '
            '"$2" "$2" "$TEST_PROFILE_HOME"\n',
            encoding="utf-8",
            newline="\n",
        )
        getent.chmod(0o700)
        environment["TEST_PROFILE_HOME"] = str(profile)
        environment["PATH"] = (
            f"{fake_bin}{os.pathsep}{environment.get('PATH', '')}"
        )

    with CELL._installation_lock(plugin_root) as lock_token:
        environment[CELL.LOCK_TOKEN_ENV] = lock_token
        environment[CELL.LOCK_ROOT_ENV] = str(plugin_root)
        environment[CELL.CELL_START_TOKEN_ENV] = lock_token
        CELL._run_cell_deploy(
            command_launcher,
            environment,
            recover=True,
        )

    captured = json.loads(capture.read_text(encoding="utf-8"))
    assert captured["argv"] == [
        "deploy",
        "--json",
        "--health-timeout",
        "30",
        "--drain-timeout",
        "30",
        "--recover",
    ]
    assert captured["environment"] == {
        CELL.LOCK_TOKEN_ENV: lock_token,
        CELL.LOCK_ROOT_ENV: str(plugin_root),
        CELL.CELL_START_TOKEN_ENV: lock_token,
        CELL.TRANSACTION_PATH_ENV: str(
            plugin_root / CELL.TRANSACTION_FILE
        ),
        CELL.TRANSACTION_TOKEN_ENV: transaction["token"],
        CELL.TRANSACTION_ID_ENV: transaction["id"],
    }
    assert service_launcher.is_file()

    capture.unlink()
    with CELL._installation_lock(plugin_root) as lock_token:
        environment[CELL.LOCK_TOKEN_ENV] = lock_token
        environment[CELL.LOCK_ROOT_ENV] = str(plugin_root)
        environment[CELL.CELL_START_TOKEN_ENV] = lock_token
        environment[CELL.TRANSACTION_TOKEN_ENV] = "f" * 64
        with pytest.raises(
            CELL.CellError,
            match="transaction reentry ownership does not match",
        ):
            CELL._run_cell_deploy(
                command_launcher,
                environment,
                recover=True,
            )
    assert not capture.exists()


def test_windows_launcher_source_is_bom_safe_for_unicode_paths(
    tmp_path: Path,
) -> None:
    source = SCRIPT.read_text(encoding="utf-8")
    assert 'encoding="utf-8-sig"' in source
    if os.name != "nt":
        return
    validated = _roots(tmp_path / "célula-索引")
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")

    service, command = CELL._write_launchers(
        validated,
        context,
        "example--1234",
        PLUGIN,
        CELL._plugin_version(PLUGIN),
        "2.0.0",
    )

    for launcher in (service, command):
        raw = launcher.read_bytes()
        assert raw.startswith(b"\xef\xbb\xbf")
        decoded = raw.decode("utf-8-sig")
        assert str(context) in decoded
        assert "célula-索引" in decoded


def test_cell_provision_lock_serializes_one_installation(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    events: list[str] = []
    first_entered = threading.Event()

    def worker(name: str, delay: float) -> None:
        with CELL._installation_lock(plugin_root):
            events.append(f"start-{name}")
            if name == "one":
                first_entered.set()
            time.sleep(delay)
            events.append(f"end-{name}")

    first = threading.Thread(target=worker, args=("one", 0.2))
    second = threading.Thread(target=worker, args=("two", 0.01))
    first.start()
    assert first_entered.wait(timeout=2)
    second.start()
    first.join()
    second.join()

    assert events == ["start-one", "end-one", "start-two", "end-two"]


def test_reentrant_lock_propagates_body_cell_error(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()

    with CELL._installation_lock(plugin_root) as token:
        with pytest.raises(CELL.CellError, match="body failure"):
            with CELL._installation_lock(
                plugin_root,
                reentry_token=token,
            ):
                raise CELL.CellError("body failure")


def test_internal_start_lock_reentry_requires_explicit_start_authority(
    monkeypatch,
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")

    with CELL._installation_lock(plugin_root) as token:
        monkeypatch.setenv(CELL.LOCK_TOKEN_ENV, token)
        monkeypatch.setenv(CELL.LOCK_ROOT_ENV, str(plugin_root))
        monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
        monkeypatch.setenv(
            "AGENT_INDEX_INSTALLATION_ID",
            "example--1234/agent-index",
        )
        monkeypatch.setenv(CELL.CELL_START_TOKEN_ENV, "not-the-lock-token")
        with pytest.raises(
            CELL.CellError,
            match="start token does not authorize lock reentry",
        ):
            CELL._internal_lock_reentry(
                PLUGIN,
                plugin_root,
                context,
                "example--1234",
                "__cell-start",
            )

        monkeypatch.setenv(CELL.CELL_START_TOKEN_ENV, token)
        assert CELL._internal_lock_reentry(
            PLUGIN,
            plugin_root,
            context,
            "example--1234",
            "__cell-start",
        ) == (token, "start", None)
        assert CELL._internal_lock_reentry(
            PLUGIN,
            plugin_root,
            context,
            "example--1234",
            "status",
        ) == (None, None, None)


def test_lock_publication_never_exposes_ownerless_destination(
    monkeypatch,
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    lock = plugin_root / ".payload-provision.lock.d"
    original_atomic = CELL._atomic_json

    def fail_owner(path: Path, value: dict[str, object]) -> None:
        if path.name == "owner.json" and path.parent.name.startswith(
            ".lock-stage-"
        ):
            raise OSError("injected owner write crash")
        original_atomic(path, value)

    monkeypatch.setattr(CELL, "_atomic_json", fail_owner)

    with pytest.raises(OSError, match="owner write crash"):
        with CELL._installation_lock(plugin_root):
            pytest.fail("ownerless lock was acquired")

    assert not lock.exists()
    assert not list(plugin_root.glob(".lock-stage-*.d"))


def test_lock_publication_stages_complete_private_owner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    lock = plugin_root / ".payload-provision.lock.d"
    receipt = {
        "schema": "copilot-extensions.agent-index.cell-lock",
        "version": 1,
        "pid": os.getpid(),
        "token": "owner-token",
    }
    original_rename = CELL._rename_directory_no_replace
    observed: dict[str, object] = {}

    def inspect_stage(source: Path, destination: Path) -> None:
        observed["owner"] = json.loads(
            (source / "owner.json").read_text(encoding="utf-8")
        )
        observed["destination_absent"] = not destination.exists()
        if os.name != "nt":
            observed["mode"] = stat.S_IMODE(source.stat().st_mode)
        original_rename(source, destination)

    monkeypatch.setattr(CELL, "_rename_directory_no_replace", inspect_stage)

    CELL._publish_owned_lock(lock, receipt)

    assert observed["owner"] == receipt
    assert observed["destination_absent"] is True
    if os.name != "nt":
        assert observed["mode"] == 0o700
    assert json.loads(
        (lock / "owner.json").read_text(encoding="utf-8")
    ) == receipt


def test_lock_restoration_is_atomic_no_replace(tmp_path: Path) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    lock = plugin_root / ".payload-provision.lock.d"
    tombstone = plugin_root / ".payload-provision.lock.stale.d"
    lock.mkdir()
    tombstone.mkdir()
    (tombstone / "owner.json").write_text(
        json.dumps({"pid": 99999999, "token": "observed"}),
        encoding="utf-8",
    )

    assert CELL._restore_moved_lock(lock, tombstone) is False
    assert lock.is_dir()
    assert not (lock / "owner.json").exists()
    assert (tombstone / "owner.json").is_file()


def test_stale_lock_reclamation_cannot_delete_new_owner(
    monkeypatch,
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    stale = plugin_root / ".payload-provision.lock.d"
    stale.mkdir()
    (stale / "owner.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.agent-index.cell-lock",
                "version": 1,
                "pid": 99999999,
                "token": "stale",
            }
        ),
        encoding="utf-8",
    )
    renamed = threading.Event()
    allow_delete = threading.Event()
    second_entered = threading.Event()
    release_second = threading.Event()
    errors: list[BaseException] = []
    original_rmtree = CELL.shutil.rmtree

    def blocking_rmtree(path, *args, **kwargs):
        if ".payload-provision.lock.stale." in Path(path).name:
            renamed.set()
            assert allow_delete.wait(timeout=5)
        return original_rmtree(path, *args, **kwargs)

    monkeypatch.setattr(CELL.shutil, "rmtree", blocking_rmtree)

    def first() -> None:
        try:
            with CELL._installation_lock(plugin_root):
                pass
        except BaseException as exc:
            errors.append(exc)

    def second() -> None:
        try:
            with CELL._installation_lock(plugin_root):
                second_entered.set()
                assert release_second.wait(timeout=5)
        except BaseException as exc:
            errors.append(exc)

    first_thread = threading.Thread(target=first)
    first_thread.start()
    assert renamed.wait(timeout=2)
    second_thread = threading.Thread(target=second)
    second_thread.start()
    assert second_entered.wait(timeout=2)
    assert stale.is_dir()
    allow_delete.set()
    time.sleep(0.05)
    assert stale.is_dir()
    release_second.set()
    first_thread.join(timeout=5)
    second_thread.join(timeout=5)

    assert not first_thread.is_alive()
    assert not second_thread.is_alive()
    assert errors == []
    assert not stale.exists()


def test_stale_lock_reclamation_rechecks_owner_after_atomic_rename(
    monkeypatch,
    tmp_path: Path,
) -> None:
    plugin_root = tmp_path / "plugin"
    plugin_root.mkdir()
    lock = plugin_root / ".payload-provision.lock.d"
    lock.mkdir()
    owner = lock / "owner.json"
    owner.write_text(
        json.dumps({"pid": 99999999, "token": "observed-stale"}),
        encoding="utf-8",
    )
    original_rename = CELL.os.rename
    original_rmtree = CELL.shutil.rmtree
    injected = False

    def rename(source, destination):
        nonlocal injected
        if Path(source) == lock and not injected:
            injected = True
            original_rmtree(lock)
            lock.mkdir()
            owner.write_text(
                json.dumps({"pid": os.getpid(), "token": "new-live-owner"}),
                encoding="utf-8",
            )
        return original_rename(source, destination)

    monkeypatch.setattr(CELL.os, "rename", rename)
    monkeypatch.setattr(CELL, "LOCK_TIMEOUT_SECONDS", 0.01)

    with pytest.raises(CELL.CellError, match="timed out waiting"):
        with CELL._installation_lock(plugin_root):
            pytest.fail("a replaced live lock was acquired")

    assert injected is True
    assert lock.is_dir()
    assert json.loads(owner.read_text(encoding="utf-8"))["token"] == "new-live-owner"


def test_service_ensure_reloads_selected_runtime_after_lock(
    monkeypatch, tmp_path: Path
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    observed_initial_validation = threading.Event()
    selected = {"version": "2.0.0"}
    reconciled: list[str] = []

    def validate(*_args, **_kwargs):
        observed_initial_validation.set()
        return validated

    def selected_runtime(*_args, **_kwargs):
        return (
            {
                "runtime": {
                    "version": selected["version"],
                    "selectedBy": {
                        "path": str(PLUGIN),
                        "version": selected["version"],
                        "snapshotId": selected["version"],
                    },
                }
            },
            PLUGIN / "python",
        )

    monkeypatch.setattr(CELL, "_validate_context", validate)
    monkeypatch.setattr(
        CELL,
        "_installation_status",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
        },
    )
    monkeypatch.setattr(CELL, "_selected_runtime", selected_runtime)
    monkeypatch.setattr(CELL, "_plugin_version", lambda _payload: "2.0.0")
    monkeypatch.setattr(
        CELL,
        "_write_launchers",
        lambda *_args, **_kwargs: (
            plugin_root / "launchers" / "service",
            plugin_root / "launchers" / "command",
        ),
    )
    monkeypatch.setattr(CELL, "_active_service", lambda *_args: None)
    monkeypatch.setattr(
        CELL,
        "_reconcile_service",
        lambda _validated, _service, _command, version, _env, _token: (
            reconciled.append(version) or {"version": version}
        ),
    )
    args = SimpleNamespace(
        context=str(context),
        durable_home=str(tmp_path),
        expected_marketplace_id="example--1234",
    )
    result: list[dict[str, object]] = []

    with CELL._installation_lock(plugin_root):
        worker = threading.Thread(
            target=lambda: result.append(CELL.service_ensure(args, PLUGIN))
        )
        worker.start()
        assert observed_initial_validation.wait(timeout=2)
        time.sleep(0.05)
        selected["version"] = "1.0.0"
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert reconciled == ["1.0.0"]
    assert result[0]["runtimeVersion"] == "1.0.0"


def test_bootstrap_does_not_reverse_rollback_published_while_waiting(
    monkeypatch, tmp_path: Path
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    observed_initial_validation = threading.Event()
    manifest = {
        "source": {"path": str(tmp_path / "stale"), "version": "1.0.0"},
        "runtime": {"version": "2.0.0"},
    }
    reconciled: list[str] = []

    def validate(*_args, **_kwargs):
        observed_initial_validation.set()
        return validated

    monkeypatch.setattr(CELL, "_validate_context", validate)
    monkeypatch.setattr(
        CELL,
        "_installation_status",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
        },
    )
    monkeypatch.setattr(CELL, "_configured_role", lambda _environment: "host")
    monkeypatch.setattr(CELL, "_plugin_version", lambda _payload: "2.0.0")
    monkeypatch.setattr(CELL, "_load_manifest", lambda *_args, **_kwargs: manifest)
    monkeypatch.setattr(
        CELL,
        "_selected_runtime",
        lambda *_args, **_kwargs: (
            manifest,
            plugin_root / "versions" / str(manifest["runtime"]["version"]) / "python",
        ),
    )
    monkeypatch.setattr(
        CELL,
        "_write_launchers",
        lambda *_args, **_kwargs: (
            plugin_root / "launchers" / "service",
            plugin_root / "launchers" / "command",
        ),
    )
    monkeypatch.setattr(
        CELL,
        "_reconcile_service",
        lambda _validated, _service, _command, version, _env, _token: (
            reconciled.append(version) or {"version": version}
        ),
    )
    monkeypatch.setattr(
        CELL,
        "_provision_locked",
        lambda *_args, **_kwargs: pytest.fail(
            "bootstrap reversed an explicit historical rollback"
        ),
    )
    args = SimpleNamespace(
        context=str(context),
        durable_home=str(tmp_path),
        expected_marketplace_id="example--1234",
    )
    result: list[dict[str, object]] = []

    with CELL._installation_lock(plugin_root):
        worker = threading.Thread(
            target=lambda: result.append(CELL.bootstrap(args, PLUGIN))
        )
        worker.start()
        assert observed_initial_validation.wait(timeout=2)
        time.sleep(0.05)
        manifest["source"] = {"path": str(PLUGIN), "version": "2.0.0"}
        manifest["runtime"] = {"version": "1.0.0"}
    worker.join(timeout=5)

    assert not worker.is_alive()
    assert reconciled == ["1.0.0"]
    assert result[0] == {
        "status": "ready",
        "provisioned": False,
        "runtimeVersion": "1.0.0",
    }


def test_real_cold_start_uses_exact_wait_and_prior_instance_signature(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    instances = Path(validated["runRoot"]) / "instances"
    instances.mkdir(parents=True)
    prior = {
        "schema": CELL.INSTANCE_SCHEMA,
        "version": 1,
        "installationId": "example--1234/agent-index",
        "runtimeVersion": "0.1.0-dev119",
        "pid": 111,
        "instanceToken": "prior-token",
        "host": "127.0.0.1",
        "port": 4111,
        "state": "stale",
        "transactionId": None,
    }
    CELL._atomic_json(instances / "111.json", prior)
    environment = CELL._cell_environment(
        validated,
        context,
        "example--1234",
    )
    environment["AGENT_INDEX_ROLE"] = "host"
    monkeypatch.setattr(CELL, "_configured_role", lambda _environment: "host")
    monkeypatch.setattr(CELL, "_active_service", lambda *_args: None)
    monkeypatch.setattr(CELL, "_retire_owned_instances", lambda *_args: 0)

    class Process:
        pid = 222

        @staticmethod
        def poll():
            return None

    if os.name == "nt":
        monkeypatch.setattr(
            CELL,
            "_spawn_windows_owned_process",
            lambda *_args, **_kwargs: Process(),
        )
    else:
        monkeypatch.setattr(
            CELL.subprocess,
            "Popen",
            lambda *_args, **_kwargs: Process(),
        )
    observed: dict[str, object] = {}

    def exact_wait(
        actual_validated: dict[str, object],
        installation_id: str,
        runtime_version: str,
        *,
        prior_instances: set[tuple[int, str]] | None = None,
        timeout: float = CELL.SERVICE_HEALTH_TIMEOUT_SECONDS,
    ) -> dict[str, object]:
        observed.update(
            {
                "validated": actual_validated,
                "installation_id": installation_id,
                "runtime_version": runtime_version,
                "prior_instances": prior_instances,
                "timeout": timeout,
            }
        )
        return {
            "port": 4222,
            "pid": 222,
            "version": runtime_version,
            "installationId": installation_id,
            "instanceToken": "new-token",
            "draining": False,
        }

    monkeypatch.setattr(CELL, "_wait_for_active_service", exact_wait)
    monkeypatch.setattr(CELL, "_reconcile_owned_instances", lambda *_args: None)

    result = CELL._reconcile_service(
        validated,
        tmp_path / "service-launcher",
        tmp_path / "command-launcher",
        "0.1.0-dev119",
        environment,
        "lock-token",
    )

    assert result is not None
    assert observed["installation_id"] == "example--1234/agent-index"
    assert observed["runtime_version"] == "0.1.0-dev119"
    assert observed["prior_instances"] == {(111, "prior-token")}


def test_transaction_cold_start_prepares_passive_before_route_commit(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    environment = CELL._cell_environment(
        validated,
        plugin_root / "install.json",
        "example--1234",
        "2.0.0+host",
    )
    environment["AGENT_INDEX_ROLE"] = "host"
    environment[CELL.TRANSACTION_TOKEN_ENV] = "transaction-token"
    monkeypatch.setattr(CELL, "_configured_role", lambda _environment: "host")
    monkeypatch.setattr(CELL, "_active_service", lambda *_args: None)
    monkeypatch.setattr(CELL, "_instance_records", lambda *_args: [])
    deploys: list[bool] = []
    monkeypatch.setattr(
        CELL,
        "_run_cell_deploy",
        lambda *_args, recover: deploys.append(recover),
    )
    monkeypatch.setattr(
        CELL,
        "_wait_for_active_service",
        lambda *_args, **_kwargs: {
            "port": 4222,
            "pid": 222,
            "version": "2.0.0+host",
            "installationId": "example--1234/agent-index",
            "instanceToken": "new-token",
            "draining": False,
        },
    )
    monkeypatch.setattr(CELL, "_reconcile_owned_instances", lambda *_args: None)
    monkeypatch.setattr(
        CELL.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail(
            "transaction cold start bypassed passive cutover"
        ),
    )

    result = CELL._reconcile_service(
        validated,
        tmp_path / "service-launcher",
        tmp_path / "command-launcher",
        "2.0.0+host",
        environment,
        "lock-token",
    )

    assert result is not None
    assert result["version"] == "2.0.0+host"
    assert deploys == [True, False]


def test_detached_service_start_is_not_ready_without_owned_health(
    monkeypatch, tmp_path: Path
) -> None:
    validated = _roots(tmp_path)
    Path(validated["pluginRoot"]).mkdir(parents=True)
    environment = CELL._cell_environment(
        validated,
        Path(validated["pluginRoot"]) / "install.json",
        "example--1234",
    )
    monkeypatch.setattr(CELL, "_configured_role", lambda _environment: "host")
    monkeypatch.setattr(CELL, "_active_service", lambda *_args: None)

    class Process:
        pid = 4321

    process = Process()
    events: list[str] = []
    monkeypatch.setattr(
        CELL,
        "_retire_owned_instances",
        lambda *_args: events.append("reconcile-before-launch") or 0,
    )
    if os.name == "nt":
        monkeypatch.setattr(
            CELL,
            "_spawn_windows_owned_process",
            lambda *_args, **_kwargs: events.append("spawn") or process,
        )
    else:
        monkeypatch.setattr(
            CELL.subprocess,
            "Popen",
            lambda *_args, **_kwargs: events.append("spawn") or process,
        )
    retired: list[object] = []

    def exact_retire(
        value,
        actual_validated,
        installation_id,
        runtime_version,
        prior_instances,
    ):
        assert actual_validated is validated
        assert installation_id == "example--1234/agent-index"
        assert runtime_version == "2.0.0"
        assert prior_instances == set()
        events.append("retire-spawn")
        retired.append(value)

    monkeypatch.setattr(CELL, "_retire_spawned_process", exact_retire)
    monkeypatch.setattr(
        CELL,
        "_wait_for_active_service",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CELL.CellError("owned health probe failed")
        ),
    )

    with pytest.raises(CELL.CellError, match="owned health probe failed"):
        CELL._reconcile_service(
            validated,
            tmp_path / "service-launcher",
            tmp_path / "command-launcher",
            "2.0.0",
            environment,
            "lock-token",
        )
    assert retired == [process]
    assert events == ["reconcile-before-launch", "spawn", "retire-spawn"]


@pytest.mark.skipif(os.name != "nt", reason="Windows direct-child ownership")
def test_windows_readiness_failure_terminates_direct_python_before_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    runtime_version = "2.0.0+host"
    slot = Path(validated["versionsRoot"]) / runtime_version
    interpreter = CELL._venv_python(slot)
    venv.EnvBuilder(with_pip=False).create(slot)
    site_result = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-X",
            "utf8",
            "-c",
            (
                "import site; print(next(p for p in site.getsitepackages() "
                "if p.endswith(('site-packages','dist-packages'))))"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    package = Path(site_result.stdout.strip()) / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text("", encoding="utf-8")
    marker = tmp_path / "pre-receipt-child.json"
    (package / "__main__.py").write_text(
        "import json, os, sys, time\n"
        "from pathlib import Path\n"
        "Path(os.environ['AGENT_INDEX_TEST_CHILD_MARKER']).write_text(\n"
        "    json.dumps({'pid': os.getpid(), 'argv': sys.argv}),\n"
        "    encoding='utf-8',\n"
        ")\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    environment = CELL._cell_environment(
        validated,
        plugin_root / "install.json",
        "example--1234",
        runtime_version,
    )
    environment["AGENT_INDEX_ROLE"] = "host"
    environment["AGENT_INDEX_TEST_CHILD_MARKER"] = str(marker)
    observed: dict[str, object] = {}
    real_popen = CELL.subprocess.Popen

    def popen(command, **kwargs):
        process = real_popen(command, **kwargs)
        observed.update(
            {
                "command": command,
                "process": process,
            }
        )
        return process

    def fail_before_receipt(*_args, **_kwargs):
        deadline = time.monotonic() + 5
        while time.monotonic() < deadline and not marker.exists():
            time.sleep(0.02)
        assert marker.exists()
        assert list((Path(validated["runRoot"]) / "instances").glob("*.json")) == []
        raise CELL.CellError("delayed readiness failed before receipt")

    monkeypatch.setattr(CELL.subprocess, "Popen", popen)
    monkeypatch.setattr(CELL, "_wait_for_active_service", fail_before_receipt)

    with pytest.raises(
        CELL.CellError,
        match="delayed readiness failed before receipt",
    ):
        CELL._reconcile_service(
            validated,
            tmp_path / "unused-service-launcher.ps1",
            tmp_path / "unused-command-launcher.ps1",
            runtime_version,
            environment,
            "lock-token",
        )

    command = observed["command"]
    process = observed["process"]
    assert isinstance(command, list)
    assert command[:8] == [
        str(interpreter),
        "-I",
        "-X",
        "utf8",
        "-m",
        "agent_index",
        "__cell-start",
    ]
    child = json.loads(marker.read_text(encoding="utf-8"))
    assert child["argv"][1:] == ["__cell-start"]
    assert process.poll() is not None
    assert not CELL._pid_alive(int(child["pid"]))


def test_readiness_failure_retires_spawned_child_receipt_not_wrapper_pid(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    instances = Path(validated["runRoot"]) / "instances"
    instances.mkdir(parents=True)
    installation_id = "example--1234/agent-index"
    child_pid = 4322
    wrapper_pid = 4321
    receipt = instances / f"{child_pid}.json"
    receipt.write_text(
        json.dumps(
            {
                "schema": CELL.INSTANCE_SCHEMA,
                "version": 1,
                "installationId": installation_id,
                "runtimeVersion": "2.0.0",
                "pid": child_pid,
                "instanceToken": "child-token",
                "host": "127.0.0.1",
                "port": 4100,
                "state": "active",
                "transactionId": None,
            }
        ),
        encoding="utf-8",
    )
    live = {wrapper_pid, child_pid}
    monkeypatch.setattr(CELL, "_pid_alive", lambda pid: pid in live)
    monkeypatch.setattr(CELL, "_service_status", lambda _port: None)
    stopped: list[int] = []

    def shutdown(record, expected_installation):
        assert expected_installation == installation_id
        stopped.append(int(record["pid"]))
        live.remove(int(record["pid"]))

    monkeypatch.setattr(CELL, "_shutdown_owned_instance", shutdown)

    class Process:
        pid = wrapper_pid

        @staticmethod
        def poll():
            return None if wrapper_pid in live else 0

        @staticmethod
        def terminate():
            live.remove(wrapper_pid)

        @staticmethod
        def wait(timeout):
            assert timeout == 5
            return 0

    CELL._retire_spawned_process(
        Process(),
        validated,
        installation_id,
        "2.0.0",
        set(),
    )

    assert stopped == [child_pid]
    assert live == set()
    assert not receipt.exists()


def test_readiness_cleanup_preserves_live_receipt_when_shutdown_fails(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    instances = Path(validated["runRoot"]) / "instances"
    instances.mkdir(parents=True)
    installation_id = "example--1234/agent-index"
    child_pid = 4322
    wrapper_pid = 4321
    record = {
        "schema": CELL.INSTANCE_SCHEMA,
        "version": 1,
        "installationId": installation_id,
        "runtimeVersion": "2.0.0",
        "pid": child_pid,
        "instanceToken": "child-token",
        "host": "127.0.0.1",
        "port": 4100,
        "state": "active",
        "transactionId": None,
    }
    receipt = instances / f"{child_pid}.json"
    receipt.write_text(json.dumps(record), encoding="utf-8")
    live = {wrapper_pid, child_pid}
    monkeypatch.setattr(CELL, "_pid_alive", lambda pid: pid in live)
    monkeypatch.setattr(
        CELL,
        "_shutdown_owned_instance",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CELL.CellError("shutdown refused")
        ),
    )
    cleared: list[list[dict[str, object]]] = []
    monkeypatch.setattr(
        CELL,
        "_clear_owned_service_evidence",
        lambda _validated, records: cleared.append(records),
    )

    class Process:
        pid = wrapper_pid

        @staticmethod
        def poll():
            return None if wrapper_pid in live else 0

        @staticmethod
        def terminate():
            live.remove(wrapper_pid)

        @staticmethod
        def wait(timeout):
            assert timeout == 5
            return 0

    with pytest.raises(CELL.CellError, match="left an owned instance running"):
        CELL._retire_spawned_process(
            Process(),
            validated,
            installation_id,
            "2.0.0",
            set(),
        )

    assert live == {child_pid}
    assert json.loads(receipt.read_text(encoding="utf-8")) == record
    assert cleared == [[]]


def test_matching_draining_service_is_not_a_ready_fast_path(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    Path(validated["pluginRoot"]).mkdir(parents=True)
    environment = CELL._cell_environment(
        validated,
        Path(validated["pluginRoot"]) / "install.json",
        "example--1234",
    )
    active = {
        "port": 4100,
        "pid": 123,
        "version": "2.0.0",
        "installationId": "example--1234/agent-index",
        "instanceToken": "instance-token",
        "draining": True,
    }
    monkeypatch.setattr(CELL, "_configured_role", lambda _environment: "host")
    monkeypatch.setattr(CELL, "_active_service", lambda *_args: active)
    monkeypatch.setattr(
        CELL,
        "_reconcile_owned_instances",
        lambda *_args: pytest.fail("draining service was accepted as ready"),
    )

    with pytest.raises(CELL.CellError, match="draining"):
        CELL._reconcile_service(
            validated,
            tmp_path / "service",
            tmp_path / "command",
            "2.0.0",
            environment,
            "lock-token",
        )


def test_transaction_recovery_may_exactly_undrain_matching_service(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    Path(validated["pluginRoot"]).mkdir(parents=True)
    environment = CELL._cell_environment(
        validated,
        Path(validated["pluginRoot"]) / "install.json",
        "example--1234",
    )
    environment[CELL.TRANSACTION_TOKEN_ENV] = "transaction-token"
    active = {
        "port": 4100,
        "pid": 123,
        "version": "2.0.0",
        "installationId": "example--1234/agent-index",
        "instanceToken": "instance-token",
        "draining": True,
    }
    monkeypatch.setattr(CELL, "_configured_role", lambda _environment: "host")
    monkeypatch.setattr(CELL, "_active_service", lambda *_args: dict(active))
    monkeypatch.setattr(CELL, "_run_cell_deploy", lambda *_args, **_kwargs: None)
    undrained: list[dict[str, object]] = []
    monkeypatch.setattr(
        CELL,
        "_undrain_owned_instance",
        lambda value, installation: (
            undrained.append({"active": value, "installation": installation})
            or {**value, "draining": False}
        ),
    )
    monkeypatch.setattr(CELL, "_reconcile_owned_instances", lambda *_args: None)

    result = CELL._reconcile_service(
        validated,
        tmp_path / "service",
        tmp_path / "command",
        "2.0.0",
        environment,
        "lock-token",
    )

    assert result is not None
    assert result["draining"] is False
    assert undrained[0]["installation"] == "example--1234/agent-index"
    assert undrained[0]["active"]["instanceToken"] == "instance-token"


def test_client_demotion_retires_owned_instances_and_evidence(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    instances = Path(validated["runRoot"]) / "instances"
    instances.mkdir(parents=True)
    installation_id = "example--1234/agent-index"
    record = {
        "schema": CELL.INSTANCE_SCHEMA,
        "version": 1,
        "installationId": installation_id,
        "runtimeVersion": "2.0.0",
        "pid": 123,
        "instanceToken": "instance-token",
        "host": "127.0.0.1",
        "port": 4100,
        "state": "active",
        "transactionId": None,
    }
    (instances / "123.json").write_text(json.dumps(record), encoding="utf-8")
    endpoint = Path(validated["runRoot"]) / "endpoint.json"
    endpoint.write_text(
        json.dumps({"schema": 1, "endpoint": "127.0.0.1:4100", "pid": 123}),
        encoding="utf-8",
    )
    (plugin_root / "running-version.json").write_text(
        json.dumps({"version": "2.0.0", "pid": 123}),
        encoding="utf-8",
    )
    from zdd import routing

    routing.publish_active(
        Path(validated["runRoot"]) / "zdd",
        bind="127.0.0.1",
        port=4100,
        pid=123,
        version="2.0.0",
    )
    live = {123}
    monkeypatch.setattr(CELL, "_pid_alive", lambda pid: pid in live)
    monkeypatch.setattr(CELL, "_configured_role", lambda _environment: "client")
    monkeypatch.setattr(
        CELL,
        "_shutdown_owned_instance",
        lambda value, expected: (
            live.remove(int(value["pid"]))
            if expected == installation_id
            else pytest.fail("foreign retirement")
        ),
    )
    environment = CELL._cell_environment(
        validated,
        plugin_root / "install.json",
        "example--1234",
    )

    result = CELL._reconcile_service(
        validated,
        tmp_path / "service",
        tmp_path / "command",
        "2.0.0",
        environment,
        "lock-token",
    )

    assert result is None
    assert not endpoint.exists()
    assert not (plugin_root / "running-version.json").exists()
    table = routing.read_table(Path(validated["runRoot"]) / "zdd") or {}
    assert "active" not in table
    assert "previous" not in table
    assert list(instances.glob("*.json")) == []


def test_client_demotion_fails_when_active_instance_has_no_owned_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    environment = CELL._cell_environment(
        validated,
        plugin_root / "install.json",
        "example--1234",
    )
    monkeypatch.setattr(CELL, "_configured_role", lambda _environment: "client")
    monkeypatch.setattr(
        CELL,
        "_active_service",
        lambda *_args: {
            "port": 4100,
            "pid": 123,
            "version": "2.0.0",
            "installationId": "example--1234/agent-index",
            "instanceToken": "unreceipted-token",
            "draining": False,
        },
    )

    with pytest.raises(CELL.CellError, match="cannot prove ownership"):
        CELL._reconcile_service(
            validated,
            tmp_path / "service",
            tmp_path / "command",
            "2.0.0",
            environment,
            "lock-token",
        )


def test_demotion_preserves_evidence_replaced_by_another_instance(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    run_root = Path(validated["runRoot"])
    plugin_root.mkdir(parents=True)
    record = {
        "schema": CELL.INSTANCE_SCHEMA,
        "version": 1,
        "installationId": "example--1234/agent-index",
        "runtimeVersion": "2.0.0",
        "pid": 123,
        "instanceToken": "old-token",
        "host": "127.0.0.1",
        "port": 4100,
        "state": "active",
        "transactionId": None,
    }
    endpoint = run_root / "endpoint.json"
    endpoint.parent.mkdir(parents=True)
    endpoint.write_text(
        json.dumps({"schema": 1, "endpoint": "127.0.0.1:4100", "pid": 123}),
        encoding="utf-8",
    )
    running = plugin_root / "running-version.json"
    running.write_text(
        json.dumps({"version": "2.0.0", "pid": 123}),
        encoding="utf-8",
    )
    from zdd import routing

    routing.publish_active(
        run_root / "zdd",
        bind="127.0.0.1",
        port=4100,
        pid=123,
        version="2.0.0",
    )
    monkeypatch.setattr(
        CELL,
        "_service_status",
        lambda _port: {
            "installationId": "example--1234/agent-index",
            "version": "2.0.0",
            "pid": 123,
            "instanceToken": "replacement-token",
        },
    )

    CELL._clear_owned_service_evidence(validated, [record])

    assert endpoint.is_file()
    assert running.is_file()
    table = routing.read_table(run_root / "zdd") or {}
    assert table.get("active", {}).get("pid") == 123


def test_service_ensure_kick_is_nonblocking_and_coalesced(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(CELL, "_validate_context", lambda *_args, **_kwargs: validated)
    monkeypatch.setattr(CELL, "_live_lock_owner", lambda _root: False)
    live = {4321}
    monkeypatch.setattr(CELL, "_pid_alive", lambda pid: pid in live)
    monkeypatch.setattr(
        CELL,
        "_process_birth_identity",
        lambda pid: "birth-4321" if pid == 4321 else None,
    )
    spawned: list[list[str]] = []

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return None

    def popen(command, **_kwargs):
        spawned.append(command)
        return Process()

    monkeypatch.setattr(CELL.subprocess, "Popen", popen)
    args = SimpleNamespace(
        context=str(context),
        durable_home=str(tmp_path),
        expected_marketplace_id="example--1234",
    )

    started_at = time.monotonic()
    first = CELL.service_ensure_kick(args, PLUGIN)
    elapsed = time.monotonic() - started_at
    second = CELL.service_ensure_kick(args, PLUGIN)

    assert elapsed < 1.0
    assert first["status"] == "started"
    assert first["started"] is True
    assert first["pid"] == 4321
    assert first["processBirth"] == "birth-4321"
    assert first["workerToken"]
    assert first["completionReceipt"].endswith(".json")
    assert second == {
        "status": "coalesced",
        "started": False,
        "pid": 4321,
        "processBirth": "birth-4321",
        "workerToken": first["workerToken"],
        "receipt": first["receipt"],
        "completionReceipt": first["completionReceipt"],
    }
    assert len(spawned) == 1
    assert "service-ensure-worker" in spawned[0]
    assert spawned[0][1:4] == ["-I", "-X", "utf8"]


def test_service_ensure_kick_rejects_worker_exit_before_owned_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(CELL, "_validate_context", lambda *_args, **_kwargs: validated)
    monkeypatch.setattr(CELL, "_live_lock_owner", lambda _root: False)
    monkeypatch.setattr(CELL, "_process_birth_identity", lambda _pid: None)

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return 1

    monkeypatch.setattr(
        CELL.subprocess,
        "Popen",
        lambda *_args, **_kwargs: Process(),
    )

    with pytest.raises(CELL.CellError, match="before its ownership receipt"):
        CELL.service_ensure_kick(
            SimpleNamespace(
                context=str(context),
                durable_home=str(tmp_path),
                expected_marketplace_id="example--1234",
            ),
            PLUGIN,
        )

    assert not CELL._ensure_worker_receipt(validated).exists()


def test_service_ensure_kick_rejects_reused_pid_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    receipt = Path(validated["runRoot"]) / "service-ensure-worker.json"
    receipt.parent.mkdir(parents=True)
    receipt.write_text(
        json.dumps(
            {
                "schema": CELL.ENSURE_WORKER_SCHEMA,
                "version": 2,
                "pid": 4321,
                "processBirth": "old-birth",
                "workerToken": "old-token",
                "context": str(context),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(CELL, "_validate_context", lambda *_args, **_kwargs: validated)
    monkeypatch.setattr(CELL, "_live_lock_owner", lambda _root: False)
    monkeypatch.setattr(
        CELL,
        "_process_birth_identity",
        lambda pid: "new-birth" if pid == 4321 else None,
    )

    class Process:
        pid = 4321

        @staticmethod
        def poll():
            return None

    spawned: list[list[str]] = []
    monkeypatch.setattr(
        CELL.subprocess,
        "Popen",
        lambda command, **_kwargs: spawned.append(command) or Process(),
    )

    result = CELL.service_ensure_kick(
        SimpleNamespace(
            context=str(context),
            durable_home=str(tmp_path),
            expected_marketplace_id="example--1234",
        ),
        PLUGIN,
    )

    assert result["status"] == "started"
    assert result["processBirth"] == "new-birth"
    assert len(spawned) == 1
    current = json.loads(receipt.read_text(encoding="utf-8"))
    assert current["processBirth"] == "new-birth"
    assert current["workerToken"] != "old-token"


def test_service_ensure_worker_clears_only_its_exact_receipt(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    receipt = Path(validated["runRoot"]) / "service-ensure-worker.json"
    receipt.parent.mkdir(parents=True)
    birth = "worker-birth"
    token = "a" * 64
    receipt.write_text(
        json.dumps(
            {
                "schema": CELL.ENSURE_WORKER_SCHEMA,
                "version": 2,
                "pid": os.getpid(),
                "processBirth": birth,
                "workerToken": token,
                "context": str(context),
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setattr(CELL, "_validate_context", lambda *_args, **_kwargs: validated)
    monkeypatch.setattr(CELL, "_process_birth_identity", lambda _pid: birth)
    monkeypatch.setattr(
        CELL,
        "service_ensure",
        lambda *_args, **_kwargs: {"status": "ready"},
    )

    result = CELL.service_ensure_worker(
        SimpleNamespace(
            context=str(context),
            durable_home=str(tmp_path),
            expected_marketplace_id="example--1234",
            worker_token=token,
        ),
        PLUGIN,
    )

    assert result == {"status": "ready"}
    assert not receipt.exists()
    completion = CELL._ensure_worker_completion_receipt(validated, token)
    value = json.loads(completion.read_text(encoding="utf-8"))
    assert value["outcome"] == "succeeded"
    assert value["workerToken"] == token
    assert value["result"] == {"status": "ready"}


def test_service_ensure_kick_returns_immediately_when_install_lock_busy(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(CELL, "_validate_context", lambda *_args, **_kwargs: validated)
    monkeypatch.setattr(CELL, "_live_lock_owner", lambda _root: True)
    monkeypatch.setattr(
        CELL.subprocess,
        "Popen",
        lambda *_args, **_kwargs: pytest.fail("lock-busy ensure launched work"),
    )

    result = CELL.service_ensure_kick(
        SimpleNamespace(
            context=str(context),
            durable_home=str(tmp_path),
            expected_marketplace_id="example--1234",
        ),
        PLUGIN,
    )

    assert result == {"status": "lock-busy", "started": False}


def test_owned_instance_reconciliation_reaps_only_attested_orphans(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    instances = Path(validated["runRoot"]) / "instances"
    instances.mkdir(parents=True)
    installation_id = "example--1234/agent-index"
    live = {101, 202, 303}
    records = {
        101: ("1.0.0", "active-token", 4101, "active"),
        202: ("0.9.0", "old-token", 4202, "active"),
        303: ("1.0.0", "passive-token", 4303, "passive"),
    }
    for pid, (version, token, port, state) in records.items():
        (instances / f"{pid}.json").write_text(
            json.dumps(
                {
                    "schema": CELL.INSTANCE_SCHEMA,
                    "version": 1,
                    "installationId": installation_id,
                    "runtimeVersion": version,
                    "pid": pid,
                    "instanceToken": token,
                    "host": "127.0.0.1",
                    "port": port,
                    "state": state,
                    "transactionId": "tx",
                }
            ),
            encoding="utf-8",
        )
    monkeypatch.setattr(CELL, "_pid_alive", lambda pid: pid in live)

    def status(port: int):
        pid = next(pid for pid, value in records.items() if value[2] == port)
        version, token, _port, state = records[pid]
        return {
            "installationId": installation_id,
            "version": version,
            "pid": pid,
            "instanceToken": token,
            "promoted": state == "active",
        }

    stopped: list[int] = []

    def shutdown(record, expected_installation):
        assert expected_installation == installation_id
        stopped.append(int(record["pid"]))
        live.remove(int(record["pid"]))

    monkeypatch.setattr(CELL, "_service_status", status)
    monkeypatch.setattr(CELL, "_shutdown_owned_instance", shutdown)

    CELL._reconcile_owned_instances(
        validated,
        {
            "installationId": installation_id,
            "pid": 101,
            "instanceToken": "active-token",
        },
    )

    assert stopped == [202, 303]
    assert [path.name for path, _record in CELL._instance_records(validated)] == [
        "101.json"
    ]
    assert plugin_root.is_dir()


def test_reconcile_owned_instances_allows_already_stopping_service_to_exit(
    monkeypatch, tmp_path: Path
) -> None:
    validated = _roots(tmp_path)
    instances = Path(validated["runRoot"]) / "instances"
    instances.mkdir(parents=True)
    installation_id = "example--1234/agent-index"
    for pid, version, token, port in (
        (101, "1.0.0", "active-token", 4101),
        (202, "0.9.0", "old-token", 4202),
    ):
        (instances / f"{pid}.json").write_text(
            json.dumps(
                {
                    "schema": CELL.INSTANCE_SCHEMA,
                    "version": 1,
                    "installationId": installation_id,
                    "runtimeVersion": version,
                    "pid": pid,
                    "instanceToken": token,
                    "host": "127.0.0.1",
                    "port": port,
                    "state": "active",
                    "transactionId": "tx",
                }
            ),
            encoding="utf-8",
        )

    old_checks = iter((True, True, False, False))

    def alive(pid: int) -> bool:
        return True if pid == 101 else next(old_checks, False)

    def status(port: int):
        if port == 4101:
            return {
                "installationId": installation_id,
                "version": "1.0.0",
                "pid": 101,
                "instanceToken": "active-token",
                "promoted": True,
            }
        return None

    monkeypatch.setattr(CELL, "_pid_alive", alive)
    monkeypatch.setattr(CELL, "_service_status", status)
    monkeypatch.setattr(CELL.time, "sleep", lambda _seconds: None)
    monkeypatch.setattr(
        CELL,
        "_shutdown_owned_instance",
        lambda *_args: pytest.fail("already-stopping service was shut down again"),
    )

    CELL._reconcile_owned_instances(
        validated,
        {
            "installationId": installation_id,
            "pid": 101,
            "instanceToken": "active-token",
        },
    )

    assert [path.name for path, _record in CELL._instance_records(validated)] == [
        "101.json"
    ]


def test_deactivation_required_never_restarts_or_provisions(
    monkeypatch, tmp_path: Path
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    monkeypatch.setattr(CELL, "_validate_context", lambda *_args, **_kwargs: validated)
    monkeypatch.setattr(
        CELL,
        "_installation_status",
        lambda *_args, **_kwargs: {
            "status": "deactivation-required",
            "reason": "deactivation-required",
            "actualMode": "namespaced",
        },
    )
    monkeypatch.setattr(
        CELL,
        "_selected_runtime",
        lambda *_args, **_kwargs: (
            {"runtime": {"version": "1.0.0"}},
            plugin_root / "versions" / "1.0.0" / "python",
        ),
    )
    monkeypatch.setattr(CELL, "_active_service", lambda *_args: None)
    monkeypatch.setattr(CELL, "_plugin_version", lambda _payload: "2.0.0")
    monkeypatch.setattr(
        CELL,
        "_validate_launcher_artifacts",
        lambda *_args, **_kwargs: (
            plugin_root / "launchers" / "service",
            plugin_root / "launchers" / "command",
        ),
    )
    monkeypatch.setattr(
        CELL,
        "_reconcile_service",
        lambda *_args, **_kwargs: pytest.fail(
            "deactivation-required service was restarted"
        ),
    )
    monkeypatch.setattr(
        CELL,
        "_provision_locked",
        lambda *_args, **_kwargs: pytest.fail(
            "deactivation-required runtime was provisioned"
        ),
    )

    result = CELL.service_ensure(
        SimpleNamespace(
            context=str(context),
            durable_home=str(tmp_path),
            expected_marketplace_id="example--1234",
        ),
        PLUGIN,
    )

    assert result["status"] == "deactivation-required"
    assert result["started"] is False


def test_manifest_does_not_accept_link_resolved_runtime_path(
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    context = plugin_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    runtime_version = "2.0.0+unconfigured"
    slot = plugin_root / "versions" / runtime_version
    interpreter = CELL._venv_python(slot)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    if os.name != "nt":
        interpreter.chmod(0o700)
    _write_runtime_slot_evidence(slot)
    alias = tmp_path / "slot-alias"
    try:
        alias.symlink_to(slot, target_is_directory=True)
    except OSError:
        pytest.skip("symbolic links are unavailable")
    manifest = {
        "schema_version": CELL.MANIFEST_SCHEMA,
        "service": "agent-index",
        "source": {
            "kind": "local",
            "path": str(PLUGIN),
            "repo": "copilot-extensions",
            "plugin": "agent-index",
            "version": "2.0.0",
            "dirty": False,
        },
        "runtime": {
            "kind": "python",
            "version": "2.0.0",
            "path": str(alias),
            "interpreter": str(CELL._venv_python(alias)),
            "selectedBy": {
                "kind": "local",
                "path": str(PLUGIN),
                "version": "2.0.0",
                "snapshotId": "2.0.0",
            },
        },
        "installation": {
            "marketplaceId": "example--1234",
            "pluginId": "agent-index",
            "installationId": "example--1234/agent-index",
            "context": str(context),
        },
    }
    manifest_path = plugin_root / "deploy-manifest.json"
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(CELL.CellError, match="runtime path escapes"):
        CELL._load_manifest(
            manifest_path,
            plugin_root,
            context,
            "example--1234",
        )


@pytest.mark.parametrize("artifact", ["manifest", "marker", "interpreter"])
def test_operational_runtime_rejects_linked_artifacts(
    monkeypatch,
    tmp_path: Path,
    artifact: str,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    context = plugin_root / "install.json"
    source = tmp_path / "payload"
    source.mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        '[project]\nname = "agent-index"\nversion = "2.0.0"\n',
        encoding="utf-8",
    )
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    runtime_version = "2.0.0+unconfigured"
    slot = plugin_root / "versions" / runtime_version
    interpreter = CELL._venv_python(slot)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    if os.name != "nt":
        interpreter.chmod(0o700)
    _write_runtime_slot_evidence(slot)
    marker = plugin_root / "current-version"
    marker.write_text(runtime_version + "\n", encoding="utf-8")
    CELL._write_manifest(
        plugin_root,
        context,
        "example--1234",
        source,
        "2.0.0",
        source,
        "2.0.0",
        "2.0.0",
        runtime_version,
        preserve_source=False,
    )
    manifest = plugin_root / "deploy-manifest.json"
    linked = {
        "manifest": manifest,
        "marker": marker,
        "interpreter": interpreter,
    }[artifact]
    target = tmp_path / f"{artifact}-target"
    if linked.is_dir():
        target.mkdir()
    else:
        target.write_bytes(linked.read_bytes())
    linked.unlink()
    try:
        linked.symlink_to(target, target_is_directory=target.is_dir())
    except OSError:
        pytest.skip("symbolic links are unavailable")
    monkeypatch.setattr(CELL, "_run_context", lambda *_args, **_kwargs: {})

    with pytest.raises(
        CELL.CellError,
        match="ordinary|link|reparse|unusable|executable",
    ):
        CELL._selected_runtime(
            source,
            validated,
            context,
            "example--1234",
            tmp_path,
        )


def test_selected_completed_runtime_requires_successful_import(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    context = plugin_root / "install.json"
    source = tmp_path / "payload"
    source.mkdir(parents=True)
    (source / "pyproject.toml").write_text(
        '[project]\nname = "agent-index"\nversion = "2.0.0"\n',
        encoding="utf-8",
    )
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    runtime_version = "2.0.0+unconfigured"
    slot = plugin_root / "versions" / runtime_version
    interpreter = CELL._venv_python(slot)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    if os.name != "nt":
        interpreter.chmod(0o700)
    _write_runtime_slot_evidence(slot)
    (plugin_root / "current-version").write_text(
        runtime_version + "\n",
        encoding="utf-8",
    )
    CELL._write_manifest(
        plugin_root,
        context,
        "example--1234",
        source,
        "2.0.0",
        source,
        "2.0.0",
        "2.0.0",
        runtime_version,
        preserve_source=False,
    )
    monkeypatch.setattr(CELL, "_run_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        CELL.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="import failed",
        ),
    )

    with pytest.raises(CELL.CellError, match="ownership-checked repair"):
        CELL._selected_runtime(
            source,
            validated,
            context,
            "example--1234",
            tmp_path,
        )


@pytest.mark.parametrize(
    ("role", "expected_target"),
    [
        ("host", "payload[store]"),
        ("client", "payload"),
        (None, "payload"),
    ],
)
def test_runtime_dependency_profile_installs_store_only_for_hosts(
    monkeypatch,
    tmp_path: Path,
    role: str | None,
    expected_target: str,
) -> None:
    payload = tmp_path / "payload"
    payload.mkdir()
    runtime_version = CELL._profile_runtime_version("2.0.0", role)
    slot = tmp_path / "cell" / "versions" / runtime_version
    interpreter = CELL._venv_python(slot)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    if os.name != "nt":
        interpreter.chmod(0o700)
    _write_runtime_slot_evidence(slot, role=role)
    commands: list[list[str]] = []
    monkeypatch.delenv("AGENT_INDEX_CELL_BUILD_SMOKE", raising=False)
    monkeypatch.setattr(
        CELL.shutil,
        "which",
        lambda command: "uv" if command == "uv" else None,
    )
    monkeypatch.setattr(
        CELL,
        "_run_checked",
        lambda command, **_kwargs: commands.append(command),
    )
    monkeypatch.setattr(
        CELL,
        "_runtime_module_path",
        lambda *_args, **_kwargs: interpreter,
    )

    result = CELL._build_runtime(
        payload,
        slot,
        marketplace_id="example--1234",
        runtime_version=runtime_version,
        role=role,
    )

    assert result == interpreter
    assert [command[-2] for command in commands] == [
        str(payload / "libs" / "zdd"),
        str(payload / "libs" / "agent-procutil"),
        str(tmp_path / expected_target),
    ]
    assert all("[engine]" not in part for command in commands for part in command)


def test_dynamic_context_loader_registers_real_completion_validator(
    tmp_path: Path,
) -> None:
    context_module = CELL._load_installation_context(PLUGIN)

    assert sys.modules[context_module.__name__] is context_module
    assert (
        context_module.validate_runtime_slot_completion.__module__
        == context_module.__name__
    )
    source = context_module.NormalizedSource(
        kind="github",
        canonical="github.com/example/example",
    )
    assert source.__class__.__module__ == context_module.__name__

    with pytest.raises(
        context_module.InstallationContextError,
        match="Runtime slot context must be absolute",
    ):
        context_module.validate_runtime_slot_completion(
            context="relative/install.json",
            expected_marketplace_id="example--aaaaaaaaaaaaaaaa",
            expected_plugin_id="agent-index",
            expected_payload_root=tmp_path,
            expected_payload_version="2.0.0",
            snapshot_id="2.0.0",
            runtime_version="2.0.0+host",
            durable_home=tmp_path / "durable",
            environment={},
        )


def test_build_completion_marker_keeps_canonical_exact_fields(
    monkeypatch,
    tmp_path: Path,
) -> None:
    snapshot = tmp_path / "snapshot"
    snapshot.mkdir(parents=True)
    (snapshot / "payload.txt").write_text("payload\n", encoding="utf-8")
    slot = tmp_path / "versions" / "2.0.0+host"
    slot.mkdir(parents=True)

    CELL._write_runtime_profile(
        slot,
        "example--1234",
        "2.0.0+host",
        "host",
    )
    monkeypatch.setattr(
        CELL,
        "_load_installation_context",
        lambda _payload: SimpleNamespace(
            _snapshot_content_sha256=lambda _snapshot: "a" * 64
        ),
    )
    CELL._write_build_receipt(
        PLUGIN,
        snapshot,
        slot,
        "2.0.0+host",
    )

    marker = json.loads(
        (slot / ".install-complete.json").read_text(encoding="utf-8")
    )
    profile = json.loads(
        (slot / CELL.RUNTIME_PROFILE_FILE).read_text(encoding="utf-8")
    )
    assert set(marker) == {"version", "completed_at", "pid", "payload_hash"}
    assert marker["version"] == "2.0.0+host"
    assert profile["profile"] == {"role": "host", "extras": ["store"]}
    assert "runtime_role" not in marker
    assert "extras" not in marker


def test_runtime_profile_receipt_is_strict_and_owned(tmp_path: Path) -> None:
    slot = tmp_path / "versions" / "2.0.0+client"
    slot.mkdir(parents=True)
    CELL._write_runtime_profile(
        slot,
        "example--1234",
        "2.0.0+client",
        "client",
    )
    profile_path = slot / CELL.RUNTIME_PROFILE_FILE
    profile = json.loads(profile_path.read_text(encoding="utf-8"))
    profile["unexpected"] = True
    profile_path.write_text(json.dumps(profile) + "\n", encoding="utf-8")

    with pytest.raises(CELL.CellError, match="profile receipt is invalid"):
        CELL._validate_runtime_profile(
            slot,
            "example--1234",
            "2.0.0+client",
            "client",
        )


@pytest.mark.parametrize(
    ("prior_role", "target_role"),
    [("client", "host"), ("host", "client")],
)
def test_same_payload_version_uses_distinct_immutable_profile_slot(
    monkeypatch,
    tmp_path: Path,
    prior_role: str,
    target_role: str,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    context = plugin_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    payload_version = "2.0.0"
    prior_runtime = CELL._profile_runtime_version(payload_version, prior_role)
    target_runtime = CELL._profile_runtime_version(payload_version, target_role)
    prior_slot = Path(validated["versionsRoot"]) / prior_runtime
    prior_slot.mkdir(parents=True)
    sentinel = prior_slot / "immutable-sentinel"
    sentinel.write_text(prior_role, encoding="utf-8")
    snapshot = Path(validated["snapshotsRoot"]) / payload_version
    snapshot.mkdir(parents=True)
    observed: dict[str, object] = {}

    monkeypatch.setattr(CELL, "_load_selection_transaction", lambda *_args: None)
    monkeypatch.setattr(CELL, "_ensure_snapshot", lambda *_args: snapshot)
    monkeypatch.setattr(
        CELL,
        "_configured_role",
        lambda _environment: target_role,
    )

    def run_context(_payload, action, *arguments):
        if action == "slot-provision":
            observed["slot_arguments"] = list(arguments)
        return {}

    def complete_slot(
        _management,
        _origin,
        _snapshot,
        _context,
        _marketplace,
        _durable,
        actual_payload_version,
        actual_runtime_version,
        slot,
        _validated,
        role,
    ):
        observed.update(
            {
                "payload_version": actual_payload_version,
                "runtime_version": actual_runtime_version,
                "slot": slot,
                "role": role,
            }
        )
        return CELL._venv_python(slot)

    def prepare(*args, **_kwargs):
        observed["transaction_runtime"] = args[8]
        return {"id": "transaction"}

    monkeypatch.setattr(CELL, "_run_context", run_context)
    monkeypatch.setattr(CELL, "_complete_slot", complete_slot)
    monkeypatch.setattr(CELL, "_prepare_selection_transaction", prepare)
    monkeypatch.setattr(
        CELL,
        "_resume_selection_transaction",
        lambda *_args: {"status": "ready", "runtimeVersion": target_runtime},
    )

    result = CELL._provision_locked(
        PLUGIN,
        PLUGIN,
        context,
        "example--1234",
        tmp_path,
        payload_version,
        validated,
        "lock-token",
    )

    slot_arguments = observed["slot_arguments"]
    assert isinstance(slot_arguments, list)
    runtime_index = slot_arguments.index("--runtime-version") + 1
    assert slot_arguments[runtime_index] == target_runtime
    assert observed["payload_version"] == payload_version
    assert observed["runtime_version"] == target_runtime
    assert observed["slot"] == Path(validated["versionsRoot"]) / target_runtime
    assert observed["role"] == target_role
    assert observed["transaction_runtime"] == target_runtime
    assert target_runtime != prior_runtime
    assert sentinel.read_text(encoding="utf-8") == prior_role
    assert result["runtimeVersion"] == target_runtime


@pytest.mark.parametrize(
    ("prior_role", "target_role"),
    [("client", "host"), ("host", "client")],
)
def test_bootstrap_profile_change_provisions_before_service_reconciliation(
    monkeypatch,
    tmp_path: Path,
    prior_role: str,
    target_role: str,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    payload_version = "2.0.0"
    manifest = {
        "source": {
            "path": str(PLUGIN.resolve()),
            "version": payload_version,
        },
        "runtime": {
            "version": CELL._profile_runtime_version(
                payload_version,
                prior_role,
            )
        },
    }
    target_runtime = CELL._profile_runtime_version(
        payload_version,
        target_role,
    )
    monkeypatch.setattr(CELL, "_validate_context", lambda *_args, **_kwargs: validated)
    monkeypatch.setattr(
        CELL,
        "_installation_status",
        lambda *_args, **_kwargs: {
            "status": "ready",
            "reason": "namespaced-active",
            "actualMode": "namespaced",
        },
    )
    monkeypatch.setattr(CELL, "_load_selection_transaction", lambda *_args: None)
    monkeypatch.setattr(CELL, "_configured_role", lambda _environment: target_role)
    monkeypatch.setattr(CELL, "_plugin_version", lambda _payload: payload_version)
    monkeypatch.setattr(CELL, "_load_manifest", lambda *_args, **_kwargs: manifest)
    monkeypatch.setattr(
        CELL,
        "_selected_runtime",
        lambda *_args, **_kwargs: (_ for _ in ()).throw(
            CELL.CellProfileMismatch("profile changed")
        ),
    )
    provisioned: list[str] = []

    def provision(*_args, **_kwargs):
        provisioned.append(target_runtime)
        return {"status": "ready", "runtimeVersion": target_runtime}

    monkeypatch.setattr(CELL, "_provision_locked", provision)
    monkeypatch.setattr(
        CELL,
        "_reconcile_service",
        lambda *_args, **_kwargs: pytest.fail(
            "bootstrap reconciled the stale dependency profile"
        ),
    )
    result = CELL.bootstrap(
        SimpleNamespace(
            context=str(context),
            durable_home=str(tmp_path),
            expected_marketplace_id="example--1234",
        ),
        PLUGIN,
    )

    assert provisioned == [target_runtime]
    assert result == {
        "status": "ready",
        "runtimeVersion": target_runtime,
        "provisioned": True,
    }


@pytest.mark.skipif(os.name == "nt", reason="POSIX venv symlink contract")
def test_uv_posix_venv_interpreter_symlink_is_accepted(
    tmp_path: Path,
) -> None:
    uv = shutil.which("uv")
    if uv is None:
        pytest.skip("uv is unavailable")
    validated = _roots(tmp_path)
    slot = Path(validated["versionsRoot"]) / "2.0.0+host"
    subprocess.run(
        [uv, "venv", str(slot)],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    interpreter = CELL._venv_python(slot)
    if not interpreter.is_symlink():
        pytest.skip("the platform venv builder copied its interpreter")
    site_result = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-X",
            "utf8",
            "-c",
            (
                "import site; print(next(p for p in site.getsitepackages() "
                "if p.endswith(('site-packages','dist-packages'))))"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    package = Path(site_result.stdout.strip()) / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text("VALUE = 1\n", encoding="utf-8")
    _write_runtime_slot_evidence(
        slot,
        runtime_version="2.0.0+host",
        role="host",
    )
    context = Path(validated["pluginRoot"]) / "install.json"
    context.write_text("{}\n", encoding="utf-8")

    CELL._runtime_import_probe(
        interpreter,
        validated,
        context,
        "example--1234",
        "2.0.0+host",
        label="selected runtime",
    )


def test_runtime_import_probe_ignores_repo_shadow_and_checks_slot_origin(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    runtime_version = "2.0.0+unconfigured"
    slot = plugin_root / "versions" / runtime_version
    interpreter = CELL._venv_python(slot)
    venv.EnvBuilder(with_pip=False).create(slot)
    site_result = subprocess.run(
        [
            str(interpreter),
            "-I",
            "-X",
            "utf8",
            "-c",
            (
                "import site; print(next(p for p in site.getsitepackages() "
                "if p.endswith(('site-packages','dist-packages'))))"
            ),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        check=True,
    )
    package = Path(site_result.stdout.strip()) / "agent_index"
    package.mkdir()
    (package / "__init__.py").write_text(
        "ORIGIN = 'selected-slot'\n",
        encoding="utf-8",
    )
    malicious = tmp_path / "malicious-repo"
    shadow = malicious / "agent_index"
    shadow.mkdir(parents=True)
    sentinel = malicious / "shadow-imported"
    (shadow / "__init__.py").write_text(
        f"from pathlib import Path\nPath({str(sentinel)!r}).write_text('bad')\n",
        encoding="utf-8",
    )
    context = plugin_root / "install.json"
    context.parent.mkdir(parents=True, exist_ok=True)
    context.write_text("{}\n", encoding="utf-8")
    _write_runtime_slot_evidence(slot)
    monkeypatch.setenv("PYTHONPATH", str(malicious))
    monkeypatch.chdir(malicious)

    CELL._runtime_import_probe(
        interpreter,
        validated,
        context,
        "example--1234",
        runtime_version,
        label="selected runtime",
    )

    assert not sentinel.exists()


def test_completed_slot_corruption_is_not_silently_overwritten(
    monkeypatch,
    tmp_path: Path,
) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    plugin_root.mkdir(parents=True)
    context = plugin_root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    payload = tmp_path / "payload"
    payload.mkdir()
    runtime_version = "2.0.0+unconfigured"
    slot = plugin_root / "versions" / runtime_version
    interpreter = CELL._venv_python(slot)
    interpreter.parent.mkdir(parents=True)
    interpreter.write_text("", encoding="utf-8")
    if os.name != "nt":
        interpreter.chmod(0o700)
    _write_runtime_slot_evidence(slot)
    completion = slot / ".runtime-slot-completion.json"
    completion.write_text("{}\n", encoding="utf-8")
    (slot / ".install-complete.json").write_text(
        json.dumps(
            {
                "version": runtime_version,
                "completed_at": "2026-01-01T00:00:00Z",
                "pid": 1,
                "payload_hash": "0" * 64,
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(CELL, "_run_context", lambda *_args, **_kwargs: {})
    monkeypatch.setattr(
        CELL,
        "_build_runtime",
        lambda *_args, **_kwargs: pytest.fail(
            "immutable completed slot was overwritten"
        ),
    )
    monkeypatch.setattr(
        CELL.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=1,
            stdout="",
            stderr="corrupt runtime",
        ),
    )

    with pytest.raises(CELL.CellError, match="ownership-checked repair"):
        CELL._complete_slot(
            PLUGIN,
            payload,
            payload,
            context,
            "example--1234",
            tmp_path,
            "2.0.0",
            runtime_version,
            slot,
            validated,
            None,
        )

    assert completion.is_file()


def test_launcher_ownership_evidence_rejects_links(tmp_path: Path) -> None:
    validated = _roots(tmp_path)
    plugin_root = Path(validated["pluginRoot"])
    context = plugin_root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    CELL._write_launchers(
        validated,
        context,
        "example--1234",
        PLUGIN,
        CELL._plugin_version(PLUGIN),
        "2.0.0",
    )
    identity = Path(validated["runRoot"]) / "service-identity.json"
    target = tmp_path / "identity-target.json"
    target.write_bytes(identity.read_bytes())
    identity.unlink()
    try:
        identity.symlink_to(target)
    except OSError:
        pytest.skip("symbolic links are unavailable")

    with pytest.raises(CELL.CellError, match="link|reparse"):
        CELL._validate_launcher_artifacts(
            validated,
            context,
            "example--1234",
            PLUGIN,
            CELL._plugin_version(PLUGIN),
            "2.0.0",
        )


def test_invalid_context_fails_without_legacy_or_global_mutation(
    tmp_path: Path,
) -> None:
    missing = tmp_path / "copied" / "install.json"
    result = subprocess.run(
        [
            sys.executable,
            str(SCRIPT),
            "service-ensure",
            "--context",
            str(missing),
            "--expected-marketplace-id",
            "example--1234",
            "--durable-home",
            str(tmp_path / "durable"),
        ],
        capture_output=True,
        text=True,
        check=False,
        env={**os.environ, "AGENT_INDEX_CELL_NO_START": "1"},
    )

    assert result.returncode != 0
    assert not (tmp_path / ".agent-index").exists()
    assert not (tmp_path / ".local").exists()
    assert not (tmp_path / ".config" / "systemd").exists()


def _authorize_private_cell_start(
    monkeypatch,
    root: Path,
    *,
    role: str = "host",
) -> str:
    token = "b" * 64
    lock = root / ".payload-provision.lock.d"
    lock.mkdir(parents=True)
    (lock / "owner.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.agent-index.cell-lock",
                "version": 1,
                "pid": os.getpid(),
                "token": token,
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("AGENT_INDEX_HOME", str(root))
    monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "example--1234/agent-index")
    monkeypatch.setenv("AGENT_INDEX_ROLE", role)
    monkeypatch.setenv("AGENT_INDEX_CELL_LOCK_ROOT", str(root))
    monkeypatch.setenv("AGENT_INDEX_CELL_LOCK_TOKEN", token)
    monkeypatch.setenv("AGENT_INDEX_CELL_START_TOKEN", token)
    for name in (
        "AGENT_INDEX_CELL_TRANSACTION",
        "AGENT_INDEX_CELL_TRANSACTION_TOKEN",
        "AGENT_INDEX_CELL_TRANSACTION_ID",
    ):
        monkeypatch.delenv(name, raising=False)
    return token


def test_public_start_and_serve_are_rejected_for_namespaced_runtime(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    monkeypatch.setenv(
        "AGENT_INDEX_INSTALLATION_ID",
        "example--1234/agent-index",
    )
    monkeypatch.setattr(
        agent_main,
        "serve",
        lambda *_args, **_kwargs: pytest.fail("public namespaced start ran"),
    )
    args = SimpleNamespace(host=None, port=None, passive=False)

    assert agent_main.cmd_start(args) == 2
    assert "public start/serve is unavailable" in capsys.readouterr().err


def test_private_cell_start_requires_host_and_live_lifecycle_lock(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "cell"
    _authorize_private_cell_start(monkeypatch, root)
    started: list[bool] = []
    monkeypatch.setattr(
        agent_main,
        "serve",
        lambda _cfg, *, passive: started.append(passive),
    )
    args = SimpleNamespace(host=None, port=None, passive=False)

    assert agent_main.cmd_cell_start(args) == 0
    assert started == [False]

    monkeypatch.setenv("AGENT_INDEX_ROLE", "client")
    assert agent_main.cmd_cell_start(args) == 2
    assert started == [False]


def test_private_passive_cell_start_requires_selection_transaction(
    monkeypatch,
    tmp_path: Path,
) -> None:
    root = tmp_path / "cell"
    _authorize_private_cell_start(monkeypatch, root)
    monkeypatch.setattr(
        agent_main,
        "serve",
        lambda *_args, **_kwargs: pytest.fail(
            "unauthorized passive service started"
        ),
    )

    assert (
        agent_main.cmd_cell_start(
            SimpleNamespace(host=None, port=None, passive=True)
        )
        == 2
    )


def test_private_cell_start_requires_reconciling_transaction_state(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    root = tmp_path / "cell"
    _authorize_private_cell_start(monkeypatch, root)
    context = root / "install.json"
    context.write_text("{}\n", encoding="utf-8")
    transaction_token = "d" * 64
    transaction = root / "selection-transaction.json"
    transaction.write_text(
        json.dumps(
            {
                "schema": agent_main.CELL_TRANSACTION_SCHEMA,
                "version": 1,
                "id": "prepared-transaction",
                "marketplaceId": "example--1234",
                "pluginId": "agent-index",
                "installationId": "example--1234/agent-index",
                "context": str(context),
                "token": transaction_token,
                "state": "prepared",
                "management": {
                    "path": str(PLUGIN),
                    "version": agent_main.__version__,
                },
                "target": {
                    "payloadRoot": str(PLUGIN),
                    "payloadVersion": agent_main.__version__,
                    "snapshotId": agent_main.__version__,
                    "runtimeVersion": agent_main.__version__,
                },
            }
        ),
        encoding="utf-8",
    )
    monkeypatch.setenv("COPILOT_EXTENSIONS_CONTEXT", str(context))
    monkeypatch.setenv("AGENT_INDEX_CELL_TRANSACTION", str(transaction))
    monkeypatch.setenv(
        "AGENT_INDEX_CELL_TRANSACTION_TOKEN",
        transaction_token,
    )
    monkeypatch.setenv(
        "AGENT_INDEX_CELL_TRANSACTION_ID",
        "prepared-transaction",
    )
    monkeypatch.setattr(
        agent_main,
        "serve",
        lambda *_args, **_kwargs: pytest.fail(
            "pre-reconciliation transaction started a service"
        ),
    )

    assert (
        agent_main.cmd_cell_start(
            SimpleNamespace(host=None, port=None, passive=False)
        )
        == 2
    )
    assert "service-reconciliation phase" in capsys.readouterr().err


def test_passive_process_publishes_no_shared_active_evidence(
    tmp_path: Path,
) -> None:
    root = tmp_path / "passive"
    installation_id = "passive/agent-index"
    lock_token = "c" * 64
    transaction_token = "d" * 64
    context = root / "install.json"
    context.parent.mkdir(parents=True)
    context.write_text("{}\n", encoding="utf-8")
    lock = root / ".payload-provision.lock.d"
    lock.mkdir()
    (lock / "owner.json").write_text(
        json.dumps(
            {
                "schema": "copilot-extensions.agent-index.cell-lock",
                "version": 1,
                "pid": os.getpid(),
                "token": lock_token,
            }
        ),
        encoding="utf-8",
    )
    transaction = root / "selection-transaction.json"
    transaction.write_text(
        json.dumps(
            {
                "schema": agent_main.CELL_TRANSACTION_SCHEMA,
                "version": 1,
                "id": "passive-transaction",
                "marketplaceId": "passive",
                "pluginId": "agent-index",
                "installationId": installation_id,
                "context": str(context),
                "token": transaction_token,
                "state": "reconciling",
                "management": {"path": str(PLUGIN), "version": "test"},
                "target": {
                    "payloadRoot": str(PLUGIN),
                    "payloadVersion": agent_main.__version__,
                    "snapshotId": agent_main.__version__,
                    "runtimeVersion": agent_main.__version__,
                },
            }
        ),
        encoding="utf-8",
    )
    environment = {
        **os.environ,
        "COPILOT_EXTENSIONS_CONTEXT": str(context),
        "AGENT_INDEX_HOME": str(root),
        "AGENT_INDEX_STATE_DIR": str(root / "state"),
        "AGENT_INDEX_DATA_DIR": str(root / "state"),
        "AGENT_INDEX_RUN_DIR": str(root / "run"),
        "AGENT_INDEX_LOG_DIR": str(root / "logs"),
        "AGENT_INDEX_CACHE_DIR": str(root / "cache"),
        "AGENT_INDEX_CONFIG_ROOT": str(root / "config"),
        "AGENT_INDEX_CONFIG": str(root / "config" / "config.yaml"),
        "AGENT_INDEX_ROUTING_DIR": str(root / "run" / "zdd"),
        "AGENT_INDEX_ENGINE_HOME": str(root / "engine"),
        "AGENT_INDEX_ENGINE_PORT": "0",
        "AGENT_INDEX_ENGINE_MODE": "external",
        "AGENT_INDEX_HOST": "127.0.0.1",
        "AGENT_INDEX_PORT": "0",
        "AGENT_INDEX_ROLE": "host",
        "AGENT_INDEX_INSTALLATION_ID": installation_id,
        "AGENT_INDEX_CELL_LOCK_ROOT": str(root),
        "AGENT_INDEX_CELL_LOCK_TOKEN": lock_token,
        "AGENT_INDEX_CELL_START_TOKEN": lock_token,
        "AGENT_INDEX_CELL_TRANSACTION": str(transaction),
        "AGENT_INDEX_CELL_TRANSACTION_TOKEN": transaction_token,
        "AGENT_INDEX_CELL_TRANSACTION_ID": "passive-transaction",
        "PYTHONUTF8": "1",
    }
    process = subprocess.Popen(
        [
            sys.executable,
            "-I",
            "-X",
            "utf8",
            "-m",
            "agent_index",
            "__cell-start",
            "--passive",
        ],
        env=environment,
        stdin=subprocess.DEVNULL,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    )
    try:
        deadline = time.monotonic() + 15
        receipt = None
        while time.monotonic() < deadline:
            paths = list((root / "run" / "instances").glob("*.json"))
            if paths:
                receipt = json.loads(paths[0].read_text(encoding="utf-8"))
                break
            time.sleep(0.1)
        assert receipt is not None
        address = f"127.0.0.1:{receipt['port']}"
        health = None
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            try:
                health = json.loads(
                    urllib.request.urlopen(
                        f"http://{address}/health",
                        timeout=1,
                    ).read()
                )
                break
            except Exception:
                time.sleep(0.1)
        assert health is not None
        assert health["status"] == "passive"
        assert health["promoted"] is False
        assert not (root / "run" / "endpoint.json").exists()
        assert not (root / "running-version.json").exists()
        assert not (root / "run" / "zdd" / "active.json").exists()
        request = urllib.request.Request(
            f"http://{address}/shutdown",
            data=b"{}",
            method="POST",
            headers={
                "X-Agent-Index-Installation-Id": installation_id,
                "X-Agent-Index-Instance-Token": str(receipt["instanceToken"]),
            },
        )
        urllib.request.urlopen(request, timeout=2).read()
        process.wait(timeout=15)
    finally:
        if process.poll() is None:
            if receipt is not None:
                try:
                    request = urllib.request.Request(
                        f"http://127.0.0.1:{receipt['port']}/shutdown",
                        data=b"{}",
                        method="POST",
                        headers={
                            "X-Agent-Index-Installation-Id": installation_id,
                            "X-Agent-Index-Instance-Token": str(
                                receipt["instanceToken"]
                            ),
                        },
                    )
                    urllib.request.urlopen(request, timeout=2).read()
                    process.wait(timeout=15)
                except Exception:
                    pass
        if process.poll() is None:
            raise AssertionError(
                f"passive Agent Index process {process.pid} did not stop gracefully"
            )


def test_two_cell_local_services_bind_distinct_os_assigned_ports(
    monkeypatch,
    tmp_path: Path,
    capsys,
) -> None:
    processes: list[subprocess.Popen[bytes]] = []
    endpoints: list[tuple[Path, int]] = []
    resolved: list[tuple[Path, int]] = []
    try:
        for name in ("one", "two"):
            root = tmp_path / name
            installation_id = f"{name}/agent-index"
            lock_token = (name[0] * 64)
            lock = root / ".payload-provision.lock.d"
            lock.mkdir(parents=True)
            (lock / "owner.json").write_text(
                json.dumps(
                    {
                        "schema": "copilot-extensions.agent-index.cell-lock",
                        "version": 1,
                        "pid": os.getpid(),
                        "token": lock_token,
                    }
                ),
                encoding="utf-8",
            )
            environment = {
                **os.environ,
                "AGENT_INDEX_HOME": str(root),
                "AGENT_INDEX_STATE_DIR": str(root / "state"),
                "AGENT_INDEX_DATA_DIR": str(root / "state"),
                "AGENT_INDEX_RUN_DIR": str(root / "run"),
                "AGENT_INDEX_LOG_DIR": str(root / "logs"),
                "AGENT_INDEX_CACHE_DIR": str(root / "cache"),
                "AGENT_INDEX_CONFIG_ROOT": str(root / "config"),
                "AGENT_INDEX_CONFIG": str(root / "config" / "config.yaml"),
                "AGENT_INDEX_ROUTING_DIR": str(root / "run" / "zdd"),
                "AGENT_INDEX_ENGINE_HOME": str(root / "engine"),
                "AGENT_INDEX_ENGINE_PORT": "0",
                "AGENT_INDEX_ENGINE_MODE": "external",
                "AGENT_INDEX_HOST": "127.0.0.1",
                "AGENT_INDEX_PORT": "0",
                "AGENT_INDEX_ROLE": "host",
                "AGENT_INDEX_INSTALLATION_ID": installation_id,
                "AGENT_INDEX_CELL_LOCK_ROOT": str(root),
                "AGENT_INDEX_CELL_LOCK_TOKEN": lock_token,
                "AGENT_INDEX_CELL_START_TOKEN": lock_token,
                "PYTHONUTF8": "1",
            }
            environment.pop("AGENT_INDEX_ENDPOINT", None)
            flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
            processes.append(
                subprocess.Popen(
                    [
                        sys.executable,
                        "-I",
                        "-X",
                        "utf8",
                        "-m",
                        "agent_index",
                        "__cell-start",
                    ],
                    env=environment,
                    stdin=subprocess.DEVNULL,
                    stdout=subprocess.DEVNULL,
                    stderr=subprocess.DEVNULL,
                    creationflags=flags,
                )
            )
            endpoints.append((root / "run" / "endpoint.json", 0))

        deadline = time.monotonic() + 20
        while time.monotonic() < deadline:
            resolved = []
            for endpoint_path, _ in endpoints:
                try:
                    record = json.loads(endpoint_path.read_text(encoding="utf-8"))
                    active_path = (
                        endpoint_path.parent / "zdd" / "active.json"
                    )
                    if not active_path.is_file():
                        break
                    resolved.append((endpoint_path, int(record["endpoint"].rsplit(":", 1)[1])))
                except (OSError, ValueError, KeyError):
                    break
            if len(resolved) == 2:
                break
            time.sleep(0.1)
        assert len(resolved) == 2
        assert resolved[0][1] != resolved[1][1]
        assert all(process.poll() is None for process in processes)
        for name in ("one", "two"):
            root = tmp_path / name
            assert (root / "run" / "zdd" / "active.json").is_file()
            assert not (root / ".local").exists()
            assert not (root / ".config" / "systemd").exists()

        first_endpoint = json.loads(
            resolved[0][0].read_text(encoding="utf-8")
        )["endpoint"]
        second_endpoint = json.loads(
            resolved[1][0].read_text(encoding="utf-8")
        )["endpoint"]
        monkeypatch.setenv("AGENT_INDEX_INSTALLATION_ID", "one/agent-index")
        monkeypatch.setattr(agent_main, "client_url", lambda: f"http://{second_endpoint}")
        assert agent_main.cmd_stop(SimpleNamespace()) == 0
        refused = json.loads(capsys.readouterr().out)
        assert refused["stopped"] is False
        assert refused["reason"] == "ownership-mismatch"
        assert processes[1].poll() is None

        request = urllib.request.Request(
            f"http://{first_endpoint}/shutdown",
            data=b"",
            method="POST",
            headers={
                "X-Agent-Index-Installation-Id": "one/agent-index",
                "X-Agent-Index-Instance-Token": str(
                    json.loads(
                        urllib.request.urlopen(
                            f"http://{first_endpoint}/health",
                            timeout=2,
                        ).read()
                    )["instanceToken"]
                ),
            },
        )
        urllib.request.urlopen(request, timeout=2).read()
        processes[0].wait(timeout=15)
        assert processes[1].poll() is None
        with urllib.request.urlopen(
            f"http://127.0.0.1:{resolved[1][1]}/health",
            timeout=2,
        ) as response:
            assert response.status == 200
    finally:
        for index, (endpoint_path, _) in enumerate(resolved):
            try:
                record = json.loads(endpoint_path.read_text(encoding="utf-8"))
                endpoint = record["endpoint"]
                health = json.loads(
                    urllib.request.urlopen(
                        f"http://{endpoint}/health",
                        timeout=2,
                    ).read()
                )
                request = urllib.request.Request(
                    f"http://{endpoint}/shutdown",
                    data=b"",
                    method="POST",
                    headers={
                        "X-Agent-Index-Installation-Id": (
                            "one/agent-index" if index == 0 else "two/agent-index"
                        ),
                        "X-Agent-Index-Instance-Token": str(
                            health["instanceToken"]
                        ),
                    },
                )
                urllib.request.urlopen(request, timeout=2).read()
            except Exception:
                pass
        for process in processes:
            try:
                process.wait(timeout=15)
            except subprocess.TimeoutExpired:
                raise AssertionError(
                    f"Agent Index process {process.pid} did not stop gracefully"
                )


def test_cross_platform_sources_keep_legacy_and_cell_lifecycle_separate() -> None:
    posix_gate = (PLUGIN / "scripts" / "runtime-gate.sh").read_text(
        encoding="utf-8"
    )
    powershell_gate = (PLUGIN / "scripts" / "runtime-gate.ps1").read_text(
        encoding="utf-8"
    )
    posix_installer = (PLUGIN / "scripts" / "install.sh").read_text(
        encoding="utf-8"
    )
    powershell_installer = (PLUGIN / "scripts" / "install.ps1").read_text(
        encoding="utf-8"
    )
    coordinator = SCRIPT.read_text(encoding="utf-8")

    for source in (posix_gate, powershell_gate):
        assert "namespaced-active" in source
        assert "requested installation context is not active" in source
        assert "installation context blocks invocation" in source
        assert "AGENT_INDEX_ENGINE_HOME" in source
        assert "AGENT_INDEX_ROUTING_DIR" in source
        assert "AGENT_INDEX_BACKUP_DIR" in source
        assert "launch-validate" in source
    assert "cell-provision" in posix_installer
    assert "cell-recover" in posix_installer
    assert "slot-cutover" in posix_installer
    assert "--expected-namespace-generation" in posix_installer
    assert "--target-payload-root" in posix_installer
    assert "--target-snapshot-id" in posix_installer
    assert "cell-provision" in powershell_installer
    assert "cell-recover" in powershell_installer
    assert "slot-cutover" in powershell_installer
    assert "ExpectedNamespaceGeneration" in powershell_installer
    assert "TargetPayloadRoot" in powershell_installer
    assert "TargetSnapshotId" in powershell_installer
    assert "systemctl" not in coordinator
    assert "ScheduledTask" not in coordinator
    assert ".local/bin" not in coordinator
    assert ".payload-provision.lock.d" in coordinator
    assert "selection-transaction.json" in coordinator
    assert "service-ensure-kick" in coordinator
    assert "AGENT_INDEX_CELL_START_TOKEN" in coordinator
    assert "service-ensure-completions" in coordinator
    assert "_rename_directory_no_replace" in coordinator
    assert 'encoding="utf-8-sig"' in coordinator


def test_posix_zombie_is_not_treated_as_alive(monkeypatch) -> None:
    if os.name == "nt":
        pytest.skip("POSIX process-state test")

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _self, **_kwargs: "123 (agent-index) Z 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20",
    )
    monkeypatch.setattr(
        CELL.os,
        "kill",
        lambda *_args: pytest.fail("zombie liveness must not fall through to os.kill"),
    )

    assert CELL._pid_alive(123) is False


def test_posix_zombie_has_no_process_birth_identity(monkeypatch) -> None:
    if os.name == "nt":
        pytest.skip("POSIX process-state test")

    monkeypatch.setattr(
        Path,
        "read_text",
        lambda _self, **_kwargs: "123 (agent-index) Z 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15 16 17 18 19 20",
    )

    assert CELL._process_birth_identity(123) is None


def test_clean_room_cleanup_treats_posix_zombies_as_exited() -> None:
    scenario_root = (
        PLUGIN.parents[1]
        / "tools"
        / "clean-room"
        / "scenarios"
        / "agent-index-installation-cells"
    )
    driver = (scenario_root / "scenario.py").read_text(encoding="utf-8")

    assert 'if tail and tail[0] == "Z":' in driver
    assert "return None" in driver


def test_clean_room_acceptance_defaults_full_and_smoke_is_diagnostic_only() -> None:
    scenario_root = (
        PLUGIN.parents[1]
        / "tools"
        / "clean-room"
        / "scenarios"
        / "agent-index-installation-cells"
    )
    manifest = json.loads(
        (scenario_root / "manifest.json").read_text(encoding="utf-8")
    )
    driver = (scenario_root / "scenario.py").read_text(encoding="utf-8")
    posix = (scenario_root / "scenario.sh").read_text(encoding="utf-8")
    powershell = (scenario_root / "scenario.ps1").read_text(encoding="utf-8")

    assert 'os.environ.get("CR_AGENT_INDEX_BUILD_MODE", "full")' in driver
    assert "return \"full\"" in driver
    assert manifest["inputs"]["CR_AGENT_INDEX_BUILD_MODE"].startswith(
        "full (default)"
    )
    assert 'cr_meta "build_mode" "$selected_build_mode"' in posix
    assert "cr_meta 'build_mode' $selectedBuildMode" in powershell
    assert 'if [[ "$selected_build_mode" == "smoke" ]]' in posix
    assert "if ($selectedBuildMode -eq 'smoke')" in powershell
    assert "smoke mode completed as a diagnostic" in posix
    assert "smoke mode completed as a diagnostic" in powershell
    assert "exercise_minimal_store" in driver
    assert "service-ensure-completion" in driver
    assert "timeout=180" in driver
    assert "trap on_exit EXIT" in posix
    assert "finally {" in powershell


def test_clean_room_crash_matrix_uses_phase_specific_durable_evidence() -> None:
    scenario_root = (
        PLUGIN.parents[1]
        / "tools"
        / "clean-room"
        / "scenarios"
        / "agent-index-installation-cells"
    )
    driver = (scenario_root / "scenario.py").read_text(encoding="utf-8")
    runtime = (PLUGIN / "src" / "agent_index" / "__main__.py").read_text(
        encoding="utf-8"
    )

    for phase, code in {
        "passive": 86,
        "flipped": 87,
        "draining": 88,
        "committed": 89,
    }.items():
        assert f'"{phase}": {code}' in driver
        assert f'"{phase}": {code}' in runtime
    assert "cutover-crash-evidence.json" in driver
    assert "cutover-crash-evidence.json" in runtime
    assert "selection-transaction.json" in driver
    assert "validate_cli=False" in driver
    assert "assert_one_owned_instance(cell_b" in driver
    assert '"instances": instance_inventory(cell)' in driver
    assert "governance-blocked-after-reconcile" not in driver
    assert '"governance-blocked"' in driver
    assert "store.upsert([chunk])" in driver
    assert "store.delete_by_source_exact('acceptance')" in driver


@pytest.mark.parametrize("shell", ["bash", "powershell"])
@pytest.mark.parametrize("exit_code", [86, 87, 88, 89])
def test_cell_installers_preserve_reserved_coordinator_exit(
    tmp_path: Path,
    shell: str,
    exit_code: int,
) -> None:
    if shell == "bash":
        if os.name == "nt":
            pytest.skip("POSIX installer wrapper test")
        executable = shutil.which("bash")
        if executable is None:
            pytest.skip("bash is unavailable")
    else:
        executable = shutil.which("powershell.exe") or shutil.which("pwsh")
        if executable is None:
            pytest.skip("PowerShell is unavailable")

    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    capture = tmp_path / f"{shell}-{exit_code}.txt"
    if os.name == "nt":
        fake_python = fake_bin / "python.cmd"
        fake_python.write_text(
            "@echo off\r\n"
            "(\r\n"
            "  echo %CD%\r\n"
            "  if defined PYTHONPATH (echo set) else (echo unset)\r\n"
            "  if defined PYTHONHOME (echo set) else (echo unset)\r\n"
            "  echo %*\r\n"
            ') > "%CAPTURE%"\r\n'
            f"exit /b {exit_code}\r\n",
            encoding="utf-8",
        )
    else:
        fake_python = fake_bin / "python"
        fake_python.write_text(
            "#!/bin/sh\n"
            "{\n"
            "  pwd\n"
            '  if [ -n "${PYTHONPATH+x}" ]; then echo set; else echo unset; fi\n'
            '  if [ -n "${PYTHONHOME+x}" ]; then echo set; else echo unset; fi\n'
            '  printf "%s\\n" "$*"\n'
            '} > "$CAPTURE"\n'
            f"exit {exit_code}\n",
            encoding="utf-8",
            newline="\n",
        )
        fake_python.chmod(0o700)
        shutil.copy2(fake_python, fake_bin / "python3")

    profile = tmp_path / "profile"
    profile.mkdir()
    environment = {
        **os.environ,
        "PATH": str(fake_bin) + os.pathsep + os.environ.get("PATH", ""),
        "CAPTURE": str(capture),
        "HOME": str(profile),
        "USERPROFILE": str(profile),
        "PYTHONPATH": str(tmp_path / "malicious-pythonpath"),
        "PYTHONHOME": str(tmp_path / "malicious-pythonhome"),
    }
    context = tmp_path / "install.json"
    if shell == "bash":
        command = [
            executable,
            str(PLUGIN / "scripts" / "install.sh"),
            "cell-recover",
            "--context",
            str(context),
            "--expected-marketplace-id",
            "example--1234",
        ]
    else:
        command = [
            executable,
            "-NoProfile",
            "-ExecutionPolicy",
            "Bypass",
            "-File",
            str(PLUGIN / "scripts" / "install.ps1"),
            "-Action",
            "cell-recover",
            "-Context",
            str(context),
            "-ExpectedMarketplaceId",
            "example--1234",
        ]
    untrusted_cwd = tmp_path / "untrusted-cwd"
    untrusted_cwd.mkdir()

    result = subprocess.run(
        command,
        cwd=untrusted_cwd,
        env=environment,
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        timeout=30,
    )

    assert result.returncode == exit_code, result.stderr
    lines = capture.read_text(encoding="utf-8").splitlines()
    assert Path(lines[0]).resolve() == PLUGIN.resolve()
    assert lines[1:3] == ["unset", "unset"]
    assert "cell-runtime.py" in lines[3]
    assert "cell-recover" in lines[3]
    assert "-I" in lines[3]
    assert "-X utf8" in lines[3]


@pytest.mark.parametrize("exit_code", [86, 87, 88, 89])
def test_cell_deploy_preserves_reserved_crash_exit(
    monkeypatch,
    tmp_path: Path,
    exit_code: int,
) -> None:
    launcher = tmp_path / "launchers" / "agent-index"
    launcher.parent.mkdir()
    launcher.write_text("", encoding="utf-8")
    monkeypatch.setattr(
        CELL.subprocess,
        "run",
        lambda *_args, **_kwargs: SimpleNamespace(
            returncode=exit_code,
            stdout="",
            stderr=f"crash-{exit_code}",
        ),
    )

    with pytest.raises(CELL.CellProcessExit) as raised:
        CELL._run_cell_deploy(launcher, {}, recover=False)

    assert raised.value.exit_code == exit_code


@pytest.mark.parametrize("exit_code", [86, 87, 88, 89])
def test_executable_cell_coordinator_preserves_reserved_exit(
    exit_code: int,
) -> None:
    program = (
        "import importlib.util, types\n"
        f"path = {str(SCRIPT)!r}\n"
        "spec = importlib.util.spec_from_file_location('cell_runtime_exec', path)\n"
        "module = importlib.util.module_from_spec(spec)\n"
        "spec.loader.exec_module(module)\n"
        "class Parser:\n"
        "    def parse_args(self, _argv):\n"
        "        return types.SimpleNamespace(action='service-ensure')\n"
        "module._parser = lambda: Parser()\n"
        "def fail(*_args, **_kwargs):\n"
        f"    raise module.CellProcessExit({exit_code}, 'reserved crash')\n"
        "module.service_ensure = fail\n"
        "raise SystemExit(module.main([]))\n"
    )

    result = subprocess.run(
        [sys.executable, "-I", "-X", "utf8", "-c", program],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        check=False,
        cwd=PLUGIN,
        env={
            key: value
            for key, value in os.environ.items()
            if key not in {"PYTHONPATH", "PYTHONHOME"}
        },
    )

    assert result.returncode == exit_code
    assert "reserved crash" in result.stderr
