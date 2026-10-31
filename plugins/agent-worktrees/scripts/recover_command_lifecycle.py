"""Inspect or explicitly qualify an interrupted, same-payload legacy reservation."""
from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
import sys
from typing import Any

sys.dont_write_bytecode = True

import command_binding as binding


def _inspect(primitive: Any, arguments: dict[str, Any]) -> tuple[dict[str, Any], dict[str, Any]]:
    root = Path(arguments["legacy_root"])
    if (
        arguments["plugin_id"] != "agent-worktrees"
        or not root.is_absolute() or not root.is_dir() or root.is_symlink()
    ):
        raise binding.CommandOwnershipConflict("Recovery requires an existing absolute runtime directory.")
    path = root / "deploy-manifest.json"
    if path.is_symlink() or not path.is_file():
        raise binding.CommandOwnershipConflict("Recovery requires an owned manifest file.")
    before = path.read_bytes()
    incumbent = primitive.read_deployment_manifest(path)
    if (
        not isinstance(incumbent, dict)
        or incumbent.get("schema_version") != 3
        or incumbent.get("service") != "agent-worktrees"
        or incumbent.get("commandBinding") is not None
    ):
        raise binding.CommandOwnershipConflict("Recovery requires an unbound Worktrees manifest.")
    source_arguments = {
        key: arguments[key] for key in ("payload_root", "plugin_id", "project_root", "environment")
    }
    provenance = primitive.resolve_payload_source(**source_arguments)
    mode = primitive.resolve_installation_mode(**arguments)
    if (
        mode["status"] != "ready"
        or mode["actualMode"] != "legacy"
        or mode["desiredMode"] != "legacy"
        or mode["legacy"]["tombstone"] is not None
        or Path(mode["runtimeRoot"]).resolve() != root.resolve()
    ):
        raise binding.CommandOwnershipConflict("Recovery requires admitted legacy installation policy.")
    if mode["marketplaceId"] != provenance["marketplaceId"]:
        raise binding.CommandOwnershipConflict(
            "Recovery installation marketplace does not match resolved provenance."
        )
    lifecycle = binding._load_lifecycle()
    lifecycle._publication(binding._rules(), primitive, arguments, lifecycle._purpose(binding._rules(), {}))
    old_owner = {
        "unattributed": True, "payloadRoot": provenance["payloadRoot"],
        "pluginId": arguments["plugin_id"], "runtimeRoot": mode["runtimeRoot"],
        "environment": mode["environment"],
    }
    reservation = incumbent.get("commandLifecycle")
    if (
        not isinstance(reservation, dict)
        or type(reservation.get("version")) is not int
        or not isinstance(reservation.get("owner"), dict)
        or reservation["owner"].get("unattributed") is not True
        or reservation != {"version": 1, "state": "pending", "owner": old_owner}
    ):
        raise binding.CommandOwnershipConflict("Recovery requires this exact pending unattributed owner.")
    receipt = binding._legacy_receipt(provenance, mode)
    owner = {key: value for key, value in receipt.items() if key != "payloadRoot"}
    repaired = {
        **incumbent, "commandLifecycle": {"version": 1, "state": "pending", "owner": owner},
    }
    if path.read_bytes() != before:
        raise binding.CommandOwnershipConflict("The manifest changed during inspection.")
    manifest_hash = "sha256:" + hashlib.sha256(before).hexdigest()
    return repaired, {
        "schema": "copilot-extensions.worktrees-lifecycle-recovery",
        "version": 1, "status": "repairable",
        "expectedEvidence": binding._identity({
            "manifestSha256": manifest_hash, "owner": owner,
            "payloadRoot": provenance["payloadRoot"],
            "projectRoot": str(Path(arguments["project_root"]).resolve()),
        }),
        "manifestSha256": manifest_hash,
        "payloadRoot": provenance["payloadRoot"], "runtimeRoot": mode["runtimeRoot"],
        "environment": mode["environment"], "source": provenance["source"],
        "sourceFingerprint": provenance["sourceFingerprint"],
        "installationId": receipt["installationId"],
        "commandReady": False, "ordinaryUpdateRequired": True,
    }


def recover(
    primitive: Any, *, expected_evidence: str | None = None, **arguments: Any,
) -> dict[str, Any]:
    """Revalidate exact evidence under the existing publication lock; never run an installer."""
    _, plan = _inspect(primitive, arguments)
    lifecycle = binding._load_lifecycle()
    lock = lifecycle._lock(primitive, arguments, timeout_seconds=0.5)
    if expected_evidence is None:
        if os.path.lexists(lock.path):
            raise binding.CommandOwnershipConflict("Publication lock exists; recovery cannot take custody.")
        return plan
    if expected_evidence != plan["expectedEvidence"]:
        raise binding.CommandOwnershipConflict("Recovery evidence changed; inspect again.")
    with lock:
        repaired, current = _inspect(primitive, arguments)
        if current != plan:
            raise binding.CommandOwnershipConflict("Recovery evidence or installation changed under the lock.")
        lock.assert_owned()
        primitive._atomic_write_json(
            Path(arguments["legacy_root"]) / "deploy-manifest.json", repaired, lock=lock,
        )
        lock.assert_owned()
    return {**plan, "status": "repaired"}


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("action", choices=("inspect", "repair"))
    parser.add_argument("--payload-root", required=True, type=Path)
    parser.add_argument("--runtime-root", required=True, type=Path)
    parser.add_argument("--project-root", required=True, type=Path)
    parser.add_argument("--expected-evidence")
    arguments = parser.parse_args(argv)
    if arguments.payload_root.resolve() != Path(__file__).resolve().parents[1]:
        parser.error("The requested payload does not own this recovery helper.")
    if (arguments.action == "repair") != (arguments.expected_evidence is not None):
        parser.error("Only repair requires --expected-evidence from a fresh inspection.")
    primitive = binding._load_primitive(arguments.payload_root)
    try:
        result = recover(
            primitive, expected_evidence=arguments.expected_evidence,
            payload_root=arguments.payload_root, plugin_id="agent-worktrees",
            legacy_root=arguments.runtime_root, project_root=arguments.project_root,
            environment={**os.environ, "COPILOT_PLUGIN_ROOT": str(arguments.payload_root)},
        )
    except (binding.CommandOwnershipConflict, primitive.InstallationContextError, OSError, ValueError) as error:
        print(f"Lifecycle recovery refused: {error}", file=sys.stderr)
        return 3
    print(json.dumps(result, sort_keys=True))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
