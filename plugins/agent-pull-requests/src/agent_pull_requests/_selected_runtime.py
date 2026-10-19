"""Shared, stdlib-only selection admission for payload and completed peer entry."""

from __future__ import annotations

import os
from pathlib import Path

PLUGIN = "agent-pull-requests"


def require_selected_metadata(
    api, *, root, runtime_version, snapshot_id, payload_root, payload_version, completed,
):
    current = api._read_runtime_marker(root / api.CURRENT_VERSION_FILE, "Current runtime")
    lkg = api._read_runtime_marker(root / api.LAST_KNOWN_GOOD_FILE, "Last known good")
    if current != runtime_version or lkg != runtime_version:
        raise PermissionError(
            "Selected runtime markers are incomplete or incoherent; repair required"
        )
    manifest = api.read_json(root / "deploy-manifest.json")
    source = manifest.get("source")
    if (
        manifest.get("schema_version") != 3 or manifest.get("service") != PLUGIN
        or manifest.get("runtime") != "python" or not isinstance(source, dict)
        or source.get("plugin") != PLUGIN or source.get("version") != payload_version
        or source.get("snapshot_id") != snapshot_id
        or source.get("content_hash") != completed["payloadSha256"]
        or not isinstance(source.get("path"), str)
        or not api.paths_equal(source["path"], payload_root)
        or not isinstance(manifest.get("venv"), str)
        or not api.paths_equal(manifest["venv"], completed["slotRoot"])
    ):
        raise PermissionError("Selected manifest differs from the completed runtime")


def select_runtime(api, status, durable, *, payload_root):
    if (status["status"], status["reason"], status["actualMode"]) != (
        "ready", "namespaced-active", "namespaced",
    ):
        raise PermissionError(
            f"Installation blocks invocation: status={status['status']} reason={status['reason']}"
        )
    validated = api.validate_context_receipt(
        status["context"], durable, expected_marketplace_id=status["marketplaceId"],
        expected_plugin_id=PLUGIN, expected_payload_root=payload_root, environment={},
    )
    root = Path(validated["pluginRoot"])
    current = api._read_runtime_marker(root / api.CURRENT_VERSION_FILE, "Current runtime")
    lkg = api._read_runtime_marker(root / api.LAST_KNOWN_GOOD_FILE, "Last known good")
    manifest_path = root / "deploy-manifest.json"
    if current is None and lkg is None and not os.path.lexists(manifest_path):
        return None
    if current is None or lkg != current:
        raise PermissionError(
            "Selected runtime markers are incomplete or incoherent; repair required"
        )
    slot = Path(validated["versionsRoot"]) / current
    owner = api.read_json(slot / api.RUNTIME_SLOT_OWNERSHIP_FILE)
    snapshot = owner.get("snapshot") if isinstance(owner, dict) else None
    snapshot_id = snapshot.get("id") if isinstance(snapshot, dict) else None
    if not isinstance(snapshot_id, str) or not snapshot_id:
        raise PermissionError("Selected slot lacks snapshot identity")
    common = dict(
        context=status["context"], durable_home=durable, expected_plugin_id=PLUGIN,
        expected_marketplace_id=status["marketplaceId"],
        snapshot_id=snapshot_id, runtime_version=current, environment={},
    )
    ownership = api.validate_runtime_slot_ownership(**common)
    provenance = api.read_json(ownership["snapshotProvenance"])
    payload = provenance["payload"]
    completed = api.validate_runtime_slot_completion(
        **common, expected_payload_root=payload["root"],
        expected_payload_version=payload["version"],
    )
    require_selected_metadata(
        api, root=root, runtime_version=current, snapshot_id=snapshot_id,
        payload_root=payload["root"], payload_version=payload["version"], completed=completed,
    )
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not python.is_file():
        raise PermissionError("Selected completed runtime lacks its interpreter")
    return python, root
