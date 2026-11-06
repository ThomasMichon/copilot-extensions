"""Transfer canonical HTTP-52 outputs, never raw capture state or executable input."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
import re
import subprocess

import agent_bridge_contract_registry as codec
from agent_bridge_contract_publication import publish
import bridge_http52_evidence as evidence

SCHEMA = "agent-bridge.http52-artifact"
MANIFEST = "manifest.json"


def _head(root: Path, source: str) -> None:
    if not re.fullmatch(r"[0-9a-f]{40}", source):
        raise ValueError("an exact source checkpoint is required")
    actual = subprocess.check_output(
        ["git", "-C", str(root), "rev-parse", "HEAD"], timeout=15, text=True,
    ).strip()
    if actual != source:
        raise ValueError("artifact source differs from the current checkpoint")


def _required(snapshot) -> set[str]:
    return {codec.REGISTRY, evidence.PRIMARY} | {
        evidence.source_fixture_path(row["path"])
        for row in evidence.contract(snapshot.materialized)["source_paths"]
        if row["semantic"]
    }


def _preimages(snapshot, outputs: dict[str, bytes]) -> dict[str, str | None]:
    inputs = {path: codec.digest(data) for path, data in snapshot.inputs.items()}
    for contract in snapshot.materialized["contracts"]:
        for row in contract["fixtures"]:
            inputs[row["path"]] = row["sha256"]
    for row in snapshot.materialized["deferred_contracts"]:
        if "reference_path" in row:
            inputs[row["reference_path"]] = row["reference_sha256"]
    primary = codec.decode(outputs[evidence.PRIMARY])
    for path, identity in primary["execution_closure"].items():
        inputs[path] = identity["sha256"]
    for path in outputs:
        inputs.setdefault(path, None)
    return inputs


def _validate(root: Path, source: str, snapshot, outputs: dict[str, bytes], *,
              exporting: bool = False) -> None:
    required = _required(snapshot)
    if not required <= outputs.keys():
        raise ValueError("artifact lacks current canonical outputs")
    for path, data in outputs.items():
        codec.read_file(root, path) if path in snapshot.inputs else evidence.safe(root, path)
        if path not in required:
            match = re.fullmatch(
                re.escape(codec.PROVENANCE) + r"[^/]+/([0-9a-f]{64})\.json", path,
            )
            if not match or match[1] != codec.digest(data):
                raise ValueError("unexpected artifact output")
            if (root / path).exists() and not exporting:
                raise ValueError("artifact cannot replace an existing provenance chunk")
    primary = codec.decode(outputs[evidence.PRIMARY])
    if (
        primary["captured_from"]["commit"] != source
        or primary["captured_from"]["protocol_generation"] != evidence.GENERATION
        or primary["checked_probes"] != evidence.PROBES
    ):
        raise ValueError("artifact capture identity differs")
    evidence.validate_outputs(root, snapshot, outputs)


def _read_outputs(directory: Path, hashes: dict) -> dict[str, bytes]:
    if not isinstance(hashes, dict) or not hashes:
        raise ValueError("artifact output inventory is invalid")
    outputs = {}
    for path, expected in hashes.items():
        if not isinstance(expected, str) or not re.fullmatch(r"[0-9a-f]{64}", expected):
            raise ValueError("artifact output hash is invalid")
        data = codec.read_file(directory, "outputs/" + path)
        if codec.digest(data) != expected:
            raise ValueError("artifact output hash mismatch")
        outputs[path] = data
    actual = set()
    for path in directory.rglob("*"):
        relative = path.relative_to(directory).as_posix()
        evidence.safe(directory, relative, directory=path.is_dir())
        if path.is_file():
            actual.add(relative)
    if actual != {MANIFEST} | {"outputs/" + path for path in hashes}:
        raise ValueError("artifact contains unexpected files")
    return outputs


def export(root: Path, source: str, directory: Path) -> str:
    _head(root, source)
    if directory.exists():
        raise ValueError("artifact destination must be fresh")
    snapshot = evidence.baseline(root, source)
    current = codec.load(root)
    paths = _required(snapshot) | (set(current.inputs) - set(snapshot.inputs))
    outputs = {path: codec.read_file(root, path) for path in sorted(paths)}
    _validate(root, source, snapshot, outputs, exporting=True)
    manifest = {
        "schema": SCHEMA, "version": 1, "source_commit": source,
        "generation": evidence.GENERATION, "case_count": sum(evidence.PROBES.values()),
        "input_preimages": _preimages(snapshot, outputs),
        "outputs": {path: codec.digest(data) for path, data in outputs.items()},
    }
    directory.mkdir()
    for path, data in outputs.items():
        target = evidence.safe(directory, "outputs/" + path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_bytes(data)
    data = codec.encode(manifest)
    (directory / MANIFEST).write_bytes(data)
    print(json.dumps({
        "source_commit": source, "generation": evidence.GENERATION,
        "cases": manifest["case_count"], "outputs": len(outputs),
        "manifest_sha256": codec.digest(data),
    }))
    return codec.digest(data)


def materialize(root: Path, source: str, directory: Path, expected_digest: str) -> None:
    _head(root, source)
    data = codec.read_file(directory, MANIFEST)
    if not re.fullmatch(r"[0-9a-f]{64}", expected_digest) or codec.digest(data) != expected_digest:
        raise ValueError("artifact receipt hash mismatch")
    manifest = codec.decode(data)
    if set(manifest) != {
        "schema", "version", "source_commit", "generation", "case_count",
        "input_preimages", "outputs",
    } or (
        manifest["schema"] != SCHEMA or type(manifest["version"]) is not int
        or manifest["version"] != 1 or manifest["source_commit"] != source
        or manifest["generation"] != evidence.GENERATION
        or manifest["case_count"] != sum(evidence.PROBES.values())
    ):
        raise ValueError("foreign artifact identity")
    outputs = _read_outputs(directory, manifest["outputs"])
    snapshot = evidence.baseline(root, source)
    _validate(root, source, snapshot, outputs)
    expected = _preimages(snapshot, outputs)
    if manifest["input_preimages"] != expected:
        raise ValueError("artifact input inventory differs from the source checkpoint")
    raw_hashes = _preimage_hashes(root, expected)
    # Native publication validates again under its OS lock and compares all raw
    # preimages before replacing current outputs; immutable chunks use no-replace.
    ordered = {path: data for path, data in outputs.items() if path != codec.REGISTRY}
    ordered[codec.REGISTRY] = outputs[codec.REGISTRY]
    publish(
        root, source, ordered,
        validate=lambda: evidence.validate_outputs(root, snapshot, ordered),
        input_hashes=raw_hashes,
    )
    print(json.dumps({"source_commit": source, "materialized_outputs": len(outputs)}))


def _preimage_hashes(root: Path, expected: dict) -> dict:
    raw_hashes = {}
    for path, digest in expected.items():
        target = evidence.safe(root, path)
        if digest is None:
            if target.exists():
                raise ValueError("artifact absent preimage now exists")
            raw_hashes[path] = None
        else:
            current = codec.read_file(root, path)
            if evidence.previous.git_evidence.sha256_bytes(current) != digest:
                raise ValueError("artifact input preimage changed")
            raw_hashes[path] = codec.digest(current)
    return raw_hashes


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("operation", choices=("export", "materialize"))
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--artifact-dir", required=True, type=Path)
    parser.add_argument("--manifest-sha256")
    args = parser.parse_args()
    root = Path(__file__).resolve().parents[1]
    directory = args.artifact_dir.absolute()
    if args.operation == "export":
        export(root, args.source_commit, directory)
    else:
        if not args.manifest_sha256:
            parser.error("materialization requires the independently verified manifest digest")
        materialize(root, args.source_commit, directory, args.manifest_sha256)


if __name__ == "__main__":
    main()
