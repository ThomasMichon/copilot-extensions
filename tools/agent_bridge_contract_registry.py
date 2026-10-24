"""Lossless provenance representation; semantic evidence remains checker-owned.

Reading never writes. Planning returns immutable chunks followed by the index;
only the existing capture/publication owners may publish that plan.
"""

from __future__ import annotations

from collections.abc import Callable
import copy
from dataclasses import dataclass
import hashlib
import json
from pathlib import Path
import re
from typing import Any

from agent_bridge_contract_paths import safe

REGISTRY = "plugins/agent-bridge/contract/registry.json"
PROVENANCE = "plugins/agent-bridge/contract/provenance/"
MAX_ARTIFACT_BYTES = 1 << 20
CHUNK_BYTES = 256 << 10
MAX_CHUNKS = 256
CHUNK_SCHEMA = "agent-bridge.contract-provenance"
Read = Callable[[str], bytes]


def encode(value: Any) -> bytes:
    return (json.dumps(
        value, ensure_ascii=False, allow_nan=False, separators=(",", ":"),
    ) + "\n").encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _pairs(items: list[tuple[str, Any]]) -> dict:
    result = {}
    for key, value in items:
        if key in result:
            raise ValueError(f"duplicate registry JSON field: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ValueError(f"non-finite registry JSON value: {value}")


def decode(data: bytes) -> dict:
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError("contract metadata exceeds the unchanged 1 MiB guard")
    value = json.loads(data, object_pairs_hook=_pairs, parse_constant=_constant)
    if not isinstance(value, dict):
        raise ValueError("contract metadata must be a JSON object")
    return value


def read_file(root: Path, relative: str) -> bytes:
    with safe(root, relative).open("rb") as stream:
        data = stream.read(MAX_ARTIFACT_BYTES + 1)
    if len(data) > MAX_ARTIFACT_BYTES:
        raise ValueError(f"contract metadata exceeds the unchanged 1 MiB guard: {relative}")
    return data


def _keys(value: Any, keys: set[str], label: str) -> None:
    if not isinstance(value, dict) or set(value) != keys:
        raise ValueError(f"{label}: missing or unknown fields")


def _chunk_path(contract: str, sha256: str) -> str:
    return f"{PROVENANCE}{contract}/{sha256}.json"


def _chunk(
    root: Path, contract: str, offset: int, descriptor: dict, read: Read,
) -> tuple[str, bytes, list]:
    _keys(descriptor, {"contract", "path", "sha256", "record_count"}, "provenance chunk")
    sha256, count = descriptor["sha256"], descriptor["record_count"]
    if (
        descriptor["contract"] != contract
        or not isinstance(sha256, str) or not re.fullmatch(r"[0-9a-f]{64}", sha256)
        or type(count) is not int or not 1 <= count <= MAX_ARTIFACT_BYTES
        or descriptor["path"] != _chunk_path(contract, sha256)
    ):
        raise ValueError("invalid provenance chunk identity/count/path")
    path = descriptor["path"]
    safe(root, path)
    data = read(path)
    if digest(data) != sha256:
        raise ValueError(f"provenance chunk hash mismatch: {path}")
    value = decode(data)
    _keys(value, {"schema", "schema_version", "contract", "record_offset", "provenance"}, path)
    if (
        value["schema"] != CHUNK_SCHEMA or type(value["schema_version"]) is not int
        or value["schema_version"] != 1 or value["contract"] != contract
        or type(value["record_offset"]) is not int or value["record_offset"] != offset
        or not isinstance(value["provenance"], list)
        or len(value["provenance"]) != count
        or any(not isinstance(row, dict) for row in value["provenance"])
        or encode(value) != data
    ):
        raise ValueError(f"invalid/noncanonical provenance chunk: {path}")
    return path, data, value["provenance"]


@dataclass(frozen=True)
class Snapshot:
    """Pinned raw inputs and complete history, without granting write authority."""

    index: dict
    materialized: dict
    inputs: dict[str, bytes]

    def verify(self, root: Path) -> None:
        for path, expected in self.inputs.items():
            if read_file(root, path) != expected:
                raise ValueError(f"contract metadata changed during capture: {path}")


def load(root: Path, *, read: Read | None = None) -> Snapshot:
    reader = read if read is not None else lambda path: read_file(root, path)
    safe(root, REGISTRY)
    data = reader(REGISTRY)
    index = decode(data)
    _keys(index, {"schema_version", "contracts", "deferred_contracts"}, "registry")
    version = index["schema_version"]
    if type(version) is not int or version not in (1, 2):
        raise ValueError("unsupported registry schema_version")
    if not isinstance(index["contracts"], list) or not index["contracts"]:
        raise ValueError("registry.contracts must be a non-empty array")
    result = copy.deepcopy(index)
    inputs = {REGISTRY: data}
    ids = set()
    total_chunks = 0
    for position, contract in enumerate(result["contracts"]):
        if not isinstance(contract, dict):
            raise ValueError("registry contract must be an object")
        name = contract.get("id")
        if (
            not isinstance(name, str) or not re.fullmatch(r"[a-z0-9][a-z0-9.-]+", name)
            or name in ids
        ):
            raise ValueError("invalid/duplicate registry contract identity")
        ids.add(name)
        rows = contract.get("provenance")
        chunks = contract.pop("provenance_chunks", [])
        if (
            not isinstance(rows, list) or not isinstance(chunks, list)
            or (version == 1 and "provenance_chunks" in index["contracts"][position])
        ):
            raise ValueError("invalid provenance representation for registry version")
        total_chunks += len(chunks)
        if total_chunks > MAX_CHUNKS:
            raise ValueError("provenance chunk inventory exceeds its bounded limit")
        prefix = []
        for descriptor in chunks:
            path, content, archived = _chunk(root, name, len(prefix), descriptor, reader)
            if path in inputs:
                raise ValueError(f"duplicate provenance chunk reference: {path}")
            inputs[path] = content
            prefix.extend(archived)
        contract["provenance"] = prefix + rows
        if not contract["provenance"]:
            raise ValueError("contract provenance must be non-empty")
    # Existing semantic validation receives the full legacy-shaped document.
    result["schema_version"] = 1
    return Snapshot(index, result, inputs)


def plan(root: Path, original: Snapshot, updated: dict) -> dict[str, bytes]:
    """Encode a complete materialization without changing any archived prefix."""
    registry = copy.deepcopy(updated)
    if registry.get("schema_version") != 1 or any(
        "provenance_chunks" in contract for contract in registry["contracts"]
    ):
        raise ValueError("publication requires a complete materialized registry")
    old = {contract["id"]: contract for contract in original.index["contracts"]}
    outputs: dict[str, bytes] = {}
    for contract in registry["contracts"]:
        chunks = copy.deepcopy(old.get(contract["id"], {}).get("provenance_chunks", []))
        count = sum(chunk["record_count"] for chunk in chunks)
        before = next(
            (c["provenance"] for c in original.materialized["contracts"]
             if c["id"] == contract["id"]), [],
        )
        if contract["provenance"][:count] != before[:count]:
            raise ValueError("capture cannot rewrite immutable provenance prefix")
        if chunks:
            contract["provenance_chunks"] = chunks
            contract["provenance"] = contract["provenance"][count:]
            registry["schema_version"] = 2
    if len(encode(registry)) > MAX_ARTIFACT_BYTES:
        registry["schema_version"] = 2
        for contract in registry["contracts"]:
            rows = contract["provenance"]
            if not rows:
                continue
            chunks = contract.setdefault("provenance_chunks", [])
            batch: list[dict] = []
            offset = sum(chunk["record_count"] for chunk in chunks)

            def archive() -> None:
                value = {
                    "schema": CHUNK_SCHEMA, "schema_version": 1,
                    "contract": contract["id"], "record_offset": offset,
                    "provenance": batch,
                }
                content = encode(value)
                if len(content) > CHUNK_BYTES:
                    raise ValueError("provenance record exceeds the chunk budget")
                sha256 = digest(content)
                path = _chunk_path(contract["id"], sha256)
                target = safe(root, path)
                if target.exists() and target.read_bytes() != content:
                    raise ValueError(f"immutable provenance path collision: {path}")
                if path in outputs or any(c["path"] == path for c in chunks):
                    raise ValueError(f"duplicate provenance chunk identity: {path}")
                outputs[path] = content
                chunks.append({
                    "contract": contract["id"], "path": path,
                    "sha256": sha256, "record_count": len(batch),
                })

            for row in rows:
                candidate = {
                    "schema": CHUNK_SCHEMA, "schema_version": 1,
                    "contract": contract["id"], "record_offset": offset,
                    "provenance": [*batch, row],
                }
                if len(encode(candidate)) > CHUNK_BYTES and batch:
                    archive()
                    offset += len(batch)
                    batch = []
                batch.append(row)
            if batch:
                archive()
            contract["provenance"] = []
    content = encode(registry)
    if len(content) > MAX_ARTIFACT_BYTES:
        raise ValueError("registry index exceeds the unchanged 1 MiB guard")
    outputs[REGISTRY] = content
    # Re-read the complete plan, including retained chunks, before handing it out.
    result = load(root, read=lambda path: outputs.get(path, original.inputs.get(path, b"")))
    if result.materialized != updated:
        raise ValueError("provenance publication is not an exact lossless round trip")
    return outputs
