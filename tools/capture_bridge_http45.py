"""Repair current HTTP-45 operation evidence after real bounded, model-free probes."""
from __future__ import annotations

import argparse
import copy
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import re
import stat
import sys
import uuid
from unittest.mock import patch

import agent_bridge_contract_git as evidence
import agent_bridge_contract_registry as registry_codec
from agent_bridge_contract_publication import publish
from bridge_http45_evidence import advertised, validate_families

ROOT = Path(__file__).resolve().parents[1]
CURRENT = "plugins/agent-bridge/contract/fixtures/http/current/"
REGISTRY = "plugins/agent-bridge/contract/registry.json"
PREFIX = "plugins/agent-bridge/src/agent_bridge/"
PRIMARY = CURRENT + "ordinary-native-admission-generation-45.json"


def read(path):
    return json.loads((ROOT / path).read_text(encoding="utf-8-sig"))


def encoded(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def same_text(actual: bytes, expected: bytes) -> bool:
    return evidence.sha256_bytes(actual) == evidence.sha256_bytes(expected)


def safe(path):
    target = ROOT / path
    if not target.resolve().is_relative_to(ROOT.resolve()) or any(
        parent.is_symlink() or getattr(parent, "is_junction", lambda: False)()
        for parent in (target, *target.parents) if parent != ROOT.parent
    ):
        raise ValueError(f"unsafe capture output: {path}")
    for node in (target, *target.parents):
        if node.exists() and getattr(node.lstat(), "st_file_attributes", 0) & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400):
            raise ValueError(f"linked capture output refused: {path}")
        if node == ROOT:
            break
    if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
        raise ValueError(f"aliased or non-file capture output refused: {path}")
    return target


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    if not re.fullmatch(r"[0-9a-f]{40}", args.source_commit):
        raise ValueError("capture requires an actual full source commit")
    if args.write:
        from agent_bridge_contract_publication import recover_pending_links
        recover_pending_links(ROOT, args.source_commit)
    snapshot = registry_codec.load(ROOT)
    registry = copy.deepcopy(snapshot.materialized)
    http = registry["contracts"][0]
    if http["declared_range"]["current"] != 45:
        raise ValueError("this capture owns only HTTP generation 45")
    version = read("plugins/agent-bridge/plugin.json")["version"]
    old_fixture_records = copy.deepcopy(http["fixtures"])
    outputs = {}
    deleted = set()
    source_rows = {row["path"]: row for row in http["source_paths"]}
    current_proof = {}
    pending_provenance = {}
    for source in http["source_paths"]:
        path = source["path"]
        data = safe(path).read_bytes()
        sha = evidence.sha256_bytes(data)
        if evidence.git_file_sha256(args.source_commit, path) != sha:
            raise ValueError(f"dirty/unproven capture source: {path}")
        blob = evidence.git_blob(args.source_commit, path)
        if blob is None:
            raise ValueError(f"capture blob unavailable: {path}")
        current_proof[path] = {
            "commit": args.source_commit, "plugin_version": version,
            "source_path": path, "source_git_blob": blob,
            "protocol_generation": 45, "source_sha256": sha,
        }

    def register(path, value, source, method):
        value = copy.deepcopy(value)
        value["captured_from"] = current_proof[source]
        outputs[path] = encoded(value)
        sha = evidence.sha256_bytes(outputs[path])
        existing = next((row for row in http["fixtures"] if row["path"] == path), None)
        record = {"path": path, "sha256": sha, "role": method, "generation": 45}
        if existing is None:
            http["fixtures"].append(record)
        else:
            existing.update(record)
        source_rows[source]["sha256"] = current_proof[source]["source_sha256"]
        provenance = {
            "commit": args.source_commit, "plugin_version": version, "generation": 45,
            "source_path": source, "source_git_blob": current_proof[source]["source_git_blob"],
            "capture_method": method,
        }
        key = tuple(provenance[name] for name in (
            "commit", "plugin_version", "generation", "source_path", "source_git_blob",
        ))
        pending_provenance[key] = provenance

    keyed = read("plugins/agent-bridge/contract/inputs/http45/native-keyed-admission.json")
    shared_method = (
        "Shared new keyed-operation evidence, replayed by bounded model-free public-HTTP, "
        "turn-journal and production client/parser probes; not independent runtime captures."
    )
    register(PRIMARY, keyed, PREFIX + "native_keyed_admission.py", shared_method)
    shared = {"path": PRIMARY, "sha256": evidence.sha256_bytes(outputs[PRIMARY])}
    for name in ("native_keyed_client.py", "session_targeting_cli.py",
                 "routes/prompts.py", "session_prompts.py", "db_schema.py", "db_events.py"):
        source = PREFIX + name
        source_rows[source].update(semantic=True, non_semantic_reason=None)
        old_path = CURRENT + "reattest-src-agent_bridge-" + name.replace("/", "-").replace(".", "-") + "-generation-45.json"
        path = CURRENT + "ordinary-native-shared-" + name.replace("/", "-").replace(".py", "") + "-generation-45.json"
        http["fixtures"] = [row for row in http["fixtures"] if row["path"] != old_path]
        if safe(old_path).exists():
            deleted.add(old_path)
        register(path, {**keyed, "shared_operation_evidence": shared}, source, shared_method)

    # Protocol already has genuine protocol-family vectors. Remove only its redundant new clone.
    redundant = CURRENT + "reattest-src-agent_bridge-protocol-py-generation-45.json"
    http["fixtures"] = [row for row in http["fixtures"] if row["path"] != redundant]
    deleted.add(redundant)

    workspace = read("plugins/agent-bridge/contract/inputs/http45/workspace-creation-cli.json")
    incorrect_workspace = CURRENT + "reattest-src-agent_bridge-workspace_creation_cli-py-generation-45.json"
    http["fixtures"] = [row for row in http["fixtures"] if row["path"] != incorrect_workspace]
    deleted.add(incorrect_workspace)
    register(CURRENT + "workspace-creation-cli-generation-45.json",
             workspace, PREFIX + "workspace_creation_cli.py",
             "Real CLI/parser/client creation builder and complete-receipt consumer, plus "
             "per-attempt floor-45 refusal; synthetic peer, no allocation/backend execution.")
    for stem in ("native-bound-extracted-routes-prompts", "managed-drain", "provider-creation-refresh"):
        previous_path = CURRENT + stem + "-generation-44.json"
        previous = read(previous_path)
        current = advertised(previous)
        current["reattested_from"] = {
            "path": previous_path, "sha256": evidence.sha256_bytes(safe(previous_path).read_bytes()),
        }
        register(CURRENT + stem + "-generation-45.json", current,
                 previous["captured_from"]["source_path"],
                 "Same-source predecessor-family preservation at HTTP-45; frozen legacy native/drain/"
                 "provider-refresh expectations are not a new runtime capture.")
    for row in pending_provenance.values():
        if (row["commit"] == args.source_commit and row["generation"] == 45
                and row["source_path"] in {PREFIX + "routes/prompts.py", PREFIX + "session_prompts.py"}):
            row["capture_method"] = (
                shared_method + " Also preserves the same-source linked unkeyed/native/drain "
                "predecessor family. These are two operation families, not two independent captures."
            )
        if row not in http["provenance"]:
            http["provenance"].append(row)

    loaded = {}
    for row in http["fixtures"]:
        data = outputs.get(row["path"])
        loaded[row["path"]] = (row["sha256"], json.loads(data) if data else read(row["path"]))
    errors = []
    validate_families(http["fixtures"], loaded, errors)
    if errors:
        raise ValueError("\n".join(errors))
    # Preserve every historical byte and every pre-existing capability floor.
    for row in old_fixture_records:
        if row["generation"] < 45 and evidence.sha256_bytes(safe(row["path"]).read_bytes()) != row["sha256"]:
            raise ValueError(f"historical fixture drift: {row['path']}")
    outputs.update(registry_codec.plan(ROOT, snapshot, registry))
    for path in (*outputs, *deleted):
        safe(path)
    probe_root = ROOT / ".test-venvs" / "http45-capture-probes" / (
        datetime.now(timezone.utc).strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex
    )
    safe(str(probe_root.relative_to(ROOT)))
    probe_root.mkdir(parents=True)
    proposed = safe(str((probe_root / "proposed-keyed.json").relative_to(ROOT)))
    proposed_bytes = outputs[PRIMARY]
    with proposed.open("xb") as stream:
        stream.write(proposed_bytes)
    proposed_sha = hashlib.sha256(proposed_bytes).hexdigest()
    from agent_procutil.owned_process import run_caller_owned
    with patch.dict(os.environ, {
        "BRIDGE_HTTP45_PROPOSED_KEYED": str(proposed),
        "BRIDGE_HTTP45_PROPOSED_SHA256": proposed_sha,
    }):
        result = run_caller_owned(
            [sys.executable, str(ROOT / "tools/run-plugin-tests.py"), "agent-bridge",
             "--test-file", "tests/test_http45_operation_evidence.py",
             "--test-file", "tests/test_native_keyed_admission.py",
             "-k", "not test_capture_rejects_tampered_proposed_keyed_before_write"],
            timeout=120, max_stdout_bytes=8 * 1024 * 1024, max_stderr_bytes=1024 * 1024,
        )
    print(result.stdout, end="")
    print(result.stderr, end="", file=sys.stderr)
    if result.returncode:
        raise ValueError("operation replay/containment failed; no capture outputs written")
    if hashlib.sha256(safe(str(proposed.relative_to(ROOT))).read_bytes()).hexdigest() != proposed_sha:
        raise ValueError("proposed operation input changed during replay; no outputs written")
    for source, proof in current_proof.items():
        if evidence.sha256_bytes(safe(source).read_bytes()) != proof["source_sha256"]:
            raise ValueError(f"source changed during capture: {source}")
    snapshot.verify(ROOT)
    if args.write:
        publish(
            ROOT, args.source_commit, outputs, validate=lambda: snapshot.verify(ROOT),
            input_hashes={
                row["path"]: registry_codec.digest(safe(row["path"]).read_bytes())
                for row in old_fixture_records
            },
        )
        for path in deleted:
            safe(path).unlink(missing_ok=True)
    else:
        for path, data in outputs.items():
            if not safe(path).is_file() or not same_text(safe(path).read_bytes(), data):
                raise ValueError(f"capture output drift: {path}")
        if any(safe(path).exists() for path in deleted):
            raise ValueError("redundant unlinked current clone still exists")
    print(json.dumps({"source_commit": args.source_commit, "outputs": len(outputs),
                      "write": args.write, "scope": "model-free operation/source evidence only"}))


if __name__ == "__main__":
    main()
