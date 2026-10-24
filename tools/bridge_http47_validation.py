"""Canonical validation of the published HTTP-47 execution source closure."""

from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path

import bridge_http47_evidence as evidence


def validate_closure(root: Path, packet: dict, registry: dict) -> None:
    if packet.get("runtime_binding_policy") != "every-owned-interpreter-source-and-entrypoint-v1":
        raise ValueError("HTTP-47 interpreter binding policy is missing")
    proof = packet.get("execution_closure")
    if not isinstance(proof, dict) or packet.get(
        "execution_closure_sha256"
    ) != evidence.digest(evidence.encode(proof)):
        raise ValueError("HTTP-47 execution closure manifest/digest missing or invalid")
    captured = packet["captured_from"]
    commit = captured["commit"]
    tree_result = evidence.git_evidence.git("ls-tree", "-r", commit)
    if tree_result.returncode == 0:
        tree = {}
        for line in tree_result.stdout.splitlines():
            metadata, path = line.split("\t", 1)
            mode, kind, blob = metadata.split()
            tree[path] = (mode, kind, blob)
        original = json.loads(evidence.git(root, "show", commit + ":" + evidence.REGISTRY))
        # Compute completeness against the captured tree, not a digest supplied
        # by the producer or a later checkout's unrelated new files.
        required = set(evidence.AFFECTED) | set(evidence.TESTS) | {
            evidence.INPUT, evidence.CONTROL_INPUT,
            "plugins/agent-bridge/plugin.json", "plugins/agent-bridge/pyproject.toml",
            evidence.CONTRACT + "registry.schema.json",
        }
        required.update(
            item["path"] for contract in original["contracts"]
            for item in contract["source_paths"]
        )
        required.update(path for path in tree if closure_python(path))
        if set(proof) != required:
            raise ValueError("HTTP-47 execution closure inventory differs from source tree")
    else:
        # A squash may orphan the source checkpoint. The complete current
        # inventory plus content-addressed blobs is the conservative fallback;
        # a changed inventory requires a fresh capture, never an opaque waiver.
        tree = None
        if set(proof) != evidence.proof_paths(
            root, json.loads(evidence.safe(root, evidence.REGISTRY).read_bytes())
        ):
            raise ValueError("HTTP-47 execution closure inventory is unprovable")
    for path, identity in proof.items():
        evidence.safe(root, path)
        if (
            not isinstance(identity, dict) or set(identity) != {"blob", "sha256"}
            or not re.fullmatch(r"[0-9a-f]{40}", identity.get("blob", ""))
            or not re.fullmatch(r"[0-9a-f]{64}", identity.get("sha256", ""))
        ):
            raise ValueError("HTTP-47 execution closure contains an invalid identity")
        blob = identity["blob"]
        if tree is not None and tree.get(path) not in {
            ("100644", "blob", blob), ("100755", "blob", blob)
        }:
            raise ValueError(f"HTTP-47 execution closure source/blob mismatch: {path}")
        actual = evidence.git_evidence.blob_sha256(blob)
        if actual is None:
            data = evidence.safe(root, path).read_bytes()
            canonical = data.decode().replace("\r\n", "\n").replace("\r", "\n").encode()
            if blob not in {
                hashlib.sha1(
                    b"blob " + str(len(value)).encode() + b"\0" + value,
                    usedforsecurity=False,
                ).hexdigest() for value in (data, canonical)
            }:
                raise ValueError(f"HTTP-47 execution closure blob is unprovable: {path}")
            actual = evidence.digest(canonical)
        if actual != identity["sha256"]:
            raise ValueError(f"HTTP-47 execution closure content mismatch: {path}")
    for source in evidence.AFFECTED:
        row = next(
            (item for contract in registry["contracts"]
             if contract["id"] == "agent-bridge.http-wire"
             for item in contract["source_paths"] if item["path"] == source), None
        )
        if row is None or row["semantic"] is not True or row["sha256"] != proof[source]["sha256"]:
            raise ValueError(f"HTTP-47 affected semantic source is not bound: {source}")
    if any(captured[key] != proof[captured["source_path"]][name] for key, name in (
        ("source_git_blob", "blob"), ("source_sha256", "sha256")
    )):
        raise ValueError("HTTP-47 primary source differs from execution closure")


def closure_python(path: str) -> bool:
    if not path.endswith(".py") or "__pycache__" in Path(path).parts:
        return False
    parts = path.split("/")
    return (
        path.startswith("plugins/agent-bridge/src/")
        or (parts[0] == "libs" and len(parts) > 3 and parts[2] == "src")
        or (parts[0] == "tools" and len(parts) == 2)
        or path.startswith(("tools/plugin_test_containment/", "tools/seasoned_uv/"))
        or (path.startswith("libs/installer-engine/") and "tests" not in parts[2:])
        or path.startswith("tools/http47_capture_bootstrap/")
    )


def validate_http47(root: Path, registry: dict, loaded: dict, errors: list[str]) -> None:
    primary = loaded.get(evidence.PRIMARY)
    if primary is None:
        errors.append("HTTP-47 execution closure primary evidence is missing")
        return
    try:
        packet = primary[1]
        if packet.get("evidence_family") != evidence.FAMILY:
            raise ValueError("HTTP-47 execution closure family mismatch")
        validate_closure(root, packet, registry)
        shared = {"path": evidence.PRIMARY, "sha256": primary[0]}
        for path in evidence.AFFECTED:
            child = loaded.get(evidence.source_fixture_path(path))
            if child is None or child[1].get("shared_operation_evidence") != shared:
                raise ValueError(f"HTTP-47 affected source lacks shared evidence: {path}")
            captured = child[1].get("captured_from", {})
            identity = packet["execution_closure"][path]
            if (
                captured.get("source_path") != path
                or captured.get("commit") != packet["captured_from"]["commit"]
                or captured.get("source_git_blob") != identity["blob"]
                or captured.get("source_sha256") != identity["sha256"]
            ):
                raise ValueError(f"HTTP-47 shared source provenance mismatch: {path}")
    except (ValueError, KeyError, TypeError, OSError) as exc:
        errors.append(f"HTTP-47 execution closure: {exc}")
