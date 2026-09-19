"""Read-only proof for the payload gate; never imports or starts the daemon."""

from __future__ import annotations

import argparse
import importlib.metadata
import importlib.util
import json
from pathlib import Path
import re
import sys

# The gate uses -I -B: only explicitly owned helper locations may be imported.
sys.path.insert(0, str(Path(__file__).resolve().parent))
import versioned_runtime as vr


def verify(
    payload: Path, root: Path, mode: str, payload_hash: str,
    context: str, generation: str, marketplace: str, action: str,
) -> dict:
    payload, root = payload.resolve(), root.resolve()
    manifest = vr._load_unique_json(payload / "plugin.json")
    version = manifest["version"]
    if manifest.get("name") != "agent-bridge" or not re.fullmatch(
        r"\d+\.\d+\.\d+(?:-dev\d+)?", version
    ):
        raise ValueError("owning payload identity/version is invalid")
    slot = root / "versions" / version
    if (root / "current-version").read_text(encoding="utf-8").strip() != version:
        raise ValueError("selected current marker does not match the payload")
    # Resolve the venv prefix, not python's symlink to a POSIX base interpreter.
    if Path(sys.prefix).resolve() != slot.resolve():
        raise ValueError("selected interpreter is not the payload's runtime slot")
    if not vr.is_complete(root, version):
        raise ValueError("selected runtime has no valid completion marker")
    if mode == "legacy":
        if not re.fullmatch(r"[0-9a-f]{64}", payload_hash) or not vr.is_complete(
            root, version, expect_hash=payload_hash
        ):
            raise ValueError("runtime completion fingerprint differs from the payload")
    else:
        sys.path.insert(0, str(payload / "scripts" / "installation-context"))
        import installation_context as ic

        context_path = Path(context)
        durable = context_path.parents[4]
        validated = ic.validate_context_receipt(
            context_path, durable, expected_marketplace_id=marketplace,
            expected_plugin_id="agent-bridge", expected_payload_root=payload,
            environment={},
        )
        if str(validated["generation"]) != generation:
            raise ValueError("installation context changed during convergence")
        ownership = ic.read_json(slot / ic.RUNTIME_SLOT_OWNERSHIP_FILE)
        completion = ic.validate_runtime_slot_completion(
            context=context, durable_home=durable,
            expected_marketplace_id=marketplace, expected_plugin_id="agent-bridge",
            expected_payload_root=payload, expected_payload_version=version,
            runtime_version=version, snapshot_id=ownership["snapshot"]["id"],
            environment={},
        )
        if Path(completion["slotRoot"]).resolve() != slot.resolve():
            raise ValueError("namespace completion does not own the selected slot")
        if completion["payloadSha256"] != ic._snapshot_content_sha256(payload):
            raise ValueError("namespace snapshot differs from the current payload")
    distribution = importlib.metadata.distribution("agent-bridge")
    if distribution.version.replace(".dev", "-dev") != version:
        raise ValueError("installed distribution version differs from the payload")
    if not Path(distribution.locate_file("")).resolve().is_relative_to(slot.resolve()):
        raise ValueError("installed distribution is outside the selected slot")
    spec = importlib.util.find_spec("agent_bridge")
    if not spec or not spec.origin or not Path(spec.origin).resolve().is_relative_to(
        slot.resolve()
    ):
        raise ValueError("agent_bridge package is outside the selected slot")
    return {
        "schema": "copilot-extensions.agent-bridge.runtime-convergence",
        "version": 1,
        "pluginId": "agent-bridge",
        "status": "ready",
        "mode": mode,
        "action": action,
        "payloadRoot": str(payload),
        "payloadVersion": version,
        "runtimeRoot": str(root),
        "runtimeVersion": version,
        "python": sys.executable,
        "complete": True,
        "currentPayload": True,
        "serviceChecked": False,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--payload", required=True, type=Path)
    parser.add_argument("--root", required=True, type=Path)
    parser.add_argument("--mode", required=True, choices=("legacy", "namespaced"))
    parser.add_argument("--payload-hash", default="")
    parser.add_argument("--context", default="")
    parser.add_argument("--generation", default="")
    parser.add_argument("--marketplace", default="")
    parser.add_argument("--action", choices=("unchanged", "updated", "provisioned"),
                        default="unchanged")
    args = parser.parse_args()
    try:
        receipt = verify(**vars(args))
    except (OSError, ValueError, KeyError, TypeError, IndexError,
            importlib.metadata.PackageNotFoundError) as error:
        print(f"[agent-bridge] current-payload verification failed: {error}", file=sys.stderr)
        return 1
    print(json.dumps(receipt))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
