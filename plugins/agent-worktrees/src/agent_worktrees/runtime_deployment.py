"""Deployment evidence from the retained installed build, not today's checkout."""

from __future__ import annotations

import json
import os
import platform
import sys
from datetime import datetime, timezone
from pathlib import Path

from packaging.version import Version


def manifest_bytes(identity: dict, slot: Path, payload_hash: str) -> bytes:
    info = identity["build_info"]
    package_version = info["version"]
    if Version(package_version) != Version(identity["package_version"]):
        raise RuntimeError("Installed provenance differs from its distribution version")
    serialized_source = info.get("deployment_source")
    if serialized_source is None:
        print(
            "Restoring legacy build provenance; payload fingerprint is unknown.",
            file=sys.stderr,
        )
        source = {
            "kind": "unknown", "path": info.get("source"), "repo": "copilot-extensions",
            "plugin": "agent-worktrees", "version": package_version,
            "commit": info.get("commit"), "branch": info.get("branch"),
            "dirty": None, "payload_fingerprint": None,
        }
    else:
        source = json.loads(serialized_source)
        if (
            not isinstance(source, dict) or source.get("version") != package_version
            or source.get("plugin") != "agent-worktrees"
            or not isinstance(source.get("path"), str)
            or not isinstance(source.get("payload_fingerprint"), str)
        ):
            raise RuntimeError("Installed deployment provenance is malformed")
    manifest = {
        "schema_version": 3,
        "service": "agent-worktrees",
        "deployed_at": datetime.now(timezone.utc).isoformat(),
        "deployed_by": f"{platform.node().lower()}-{platform.system().lower()}",
        "source": source,
        "venv": slot.as_posix(),
        "runtime": "python",
        "runtime_slot": slot.name,
        "runtime_generation": info.get("runtime_generation"),
        "payload_hash": payload_hash,
    }
    if "runtime_source" in source:
        document = source["runtime_source"]
        if (
            not isinstance(document, dict) or document.get("plugin") != "agent-worktrees"
            or document.get("package_version") != package_version
            or source.get("runtime_version") != document.get("runtime_version")
        ):
            raise RuntimeError("Installed source-qualified deployment provenance is malformed")
        manifest.update(runtime_version=slot.name, runtime_source=document)
    return (json.dumps(manifest, indent=2) + "\n").encode("utf-8")


def read_manifest(root: Path) -> bytes | None:
    try:
        return (root / "deploy-manifest.json").read_bytes()
    except FileNotFoundError:
        return None


def publish(root: Path, content: bytes) -> None:
    path = root / "deploy-manifest.json"
    temporary = path.with_name(f"{path.name}.tmp-{os.getpid()}")
    try:
        temporary.write_bytes(content)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def restore(root: Path, snapshot: bytes | None, candidate: bytes) -> None:
    if read_manifest(root) not in (snapshot, candidate):
        raise RuntimeError("Deployment evidence changed before rollback")
    if snapshot is None:
        (root / "deploy-manifest.json").unlink(missing_ok=True)
    else:
        publish(root, snapshot)
