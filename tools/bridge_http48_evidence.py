"""HTTP-48 local retained-inbox evidence; historical packets are never rewritten."""

from __future__ import annotations

import copy
import hashlib
import json
from pathlib import Path
import re

import bridge_http47_evidence as previous
import bridge_http47_validation as historical
import bridge_http48_installation as installation

CONTRACT = previous.CONTRACT
REGISTRY = previous.REGISTRY
INPUT = CONTRACT + "inputs/http48/retained-inbox.json"
PRIMARY = previous.CURRENT + "http48-retained-inbox.json"
FAMILY = "http48-retained-inbox-asgi-v1"
AFFECTED = tuple(previous.PREFIX + name for name in (
    "protocol.py", "app_routes.py", "live_inbox_models.py", "live_inbox_runtime.py",
    "routes/live_inbox.py", "db_inbox_custody.py", "db_live_sessions.py",
    "inbox_custody.py", "loop_governance.py", "live_message_enqueue.py", "routes/live_sessions.py",
    "db_live_session_aliases.py", "db_live_message_receipts.py",
    "plugin_readiness.py", "routes/live_startup_controls.py",
    "db_core.py", "db_schema.py", "inbox_consumer_schema.py",
))
TEST = "plugins/agent-bridge/tests/test_http48_capture_probes.py"
EXTRA_PATHS = (INPUT, TEST, "plugins/agent-bridge/tests/test_live_inbox_runtime.py",
               "plugins/agent-bridge/tests/test_loop_governance_custody.py")
PROBES = {"test_http48_capture_actual_observations": 2}
POLICY = "every-owned-interpreter-source-and-entrypoint-v1"
encode = previous.encode
digest = previous.digest
safe = previous.safe


def contract(registry: dict) -> dict:
    return next(c for c in registry["contracts"] if c["id"] == "agent-bridge.http-wire")


def baseline(root: Path, commit: str) -> previous.registry_codec.Snapshot:
    snapshot = previous.baseline_snapshot(root, commit, generation=48)
    protocol = previous.git(root, "show", commit + ":" + previous.PREFIX + "protocol.py")
    if previous.constant(protocol, "RETAINED_INBOX_PROTOCOL_VERSION") != 48:
        raise ValueError("HTTP-48 retained-inbox capability floor changed")
    return snapshot


def source_snapshot(root: Path, commit: str, registry: dict) -> dict:
    baseline(root, commit)
    return previous.source_snapshot(
        root, commit, registry, generation=48,
        extra_paths=(*EXTRA_PATHS, *installation.paths(root)),
    )


def source_fixture_path(source: str) -> str:
    return previous.source_fixture_path(source).replace("http47-source-", "http48-source-")


def history(
    root: Path, registry: dict, outputs: dict[str, bytes] | None = None,
) -> dict[str, str]:
    result = {
        path: digest(data) for path, data in previous.registry_codec.load(root).inputs.items()
    }
    for owner in registry["contracts"]:
        for fixture in owner["fixtures"]:
            data = safe(root, fixture["path"]).read_bytes()
            allowed = {fixture["sha256"]}
            if (owner["id"] == "agent-bridge.http-wire" and fixture["generation"] == 48
                    and outputs is not None and fixture["path"] in outputs):
                allowed.add(digest(outputs[fixture["path"]]))
            if previous.git_evidence.sha256_bytes(data) not in allowed:
                raise ValueError(f"immutable baseline fixture drift: {fixture['path']}")
            result[fixture["path"]] = digest(data)
    for row in registry["deferred_contracts"]:
        if "reference_path" in row:
            data = safe(root, row["reference_path"]).read_bytes()
            if previous.git_evidence.sha256_bytes(data) != row["reference_sha256"]:
                raise ValueError("immutable deferred reference drift")
            result[row["reference_path"]] = digest(data)
    return result


def build_outputs(
    root: Path, commit: str, snapshot: previous.registry_codec.Snapshot,
    proof: dict, observed: dict,
) -> dict[str, bytes]:
    # Object insertion order is not an observation; array order still is.
    observed = json.loads(json.dumps(observed, sort_keys=True, allow_nan=False))
    registry = copy.deepcopy(snapshot.materialized)
    http = contract(registry)
    version = json.loads(safe(root, "plugins/agent-bridge/plugin.json").read_bytes())["version"]
    sources = {row["path"]: row for row in http["source_paths"]}
    if not set(AFFECTED) <= sources.keys():
        raise ValueError("HTTP-48 semantic owners must be registered before capture")
    for source, row in sources.items():
        if row["sha256"] != proof[source]["sha256"]:
            raise ValueError(f"unclassified HTTP-48 source drift: {source}")
        if source in AFFECTED and row["semantic"] is not True:
            raise ValueError(f"HTTP-48 affected source is not semantic: {source}")
    if 48 not in http["evidence_window"]["generations"]:
        http["evidence_window"]["generations"].append(48)
    if version not in http["evidence_window"]["runtimes"]:
        http["evidence_window"]["runtimes"].append(version)
    outputs: dict[str, bytes] = {}

    def register(path: str, packet: dict, source: str, method: str) -> None:
        packet["captured_from"] = {
            "commit": commit, "plugin_version": version, "protocol_generation": 48,
            "source_path": source, "source_git_blob": proof[source]["blob"],
            "source_sha256": proof[source]["sha256"],
        }
        data = encode(packet)
        outputs[path] = data
        old = next((row for row in http["fixtures"] if row["path"] == path), None)
        if old is not None:
            current = safe(root, path).read_bytes()
            owned = json.loads(current)
            upgrade = (
                source in AFFECTED
                and owned.get("evidence_family") == "http48-unchanged-source-link-v1"
                and packet["evidence_family"] == "http48-shared-asgi-v1"
            )
            if (old["generation"] != 48
                    or owned.get("captured_from", {}).get("source_path") != source
                    or owned.get("captured_from", {}).get("protocol_generation") != 48
                    or (owned.get("evidence_family") != packet["evidence_family"] and not upgrade)
                    or previous.git_evidence.sha256_bytes(current) not in {
                        old["sha256"], digest(data),
                    }):
                raise ValueError(f"HTTP-48 current output is dirty or not owned: {path}")
        record = {
            "path": path, "sha256": digest(data), "role": method, "generation": 48,
        }
        if old is None:
            if safe(root, path).exists() and safe(root, path).read_bytes() != data:
                raise ValueError(f"HTTP-48 unregistered output already exists: {path}")
            http["fixtures"].append(record)
        else:
            old.update(record)
        provenance = {
            "commit": commit, "plugin_version": version, "generation": 48,
            "source_path": source, "source_git_blob": proof[source]["blob"],
            "capture_method": method,
        }
        if provenance not in http["provenance"]:
            http["provenance"].append(provenance)

    register(PRIMARY, {
        "evidence_family": FAMILY, "observations": observed,
        "execution_closure": proof, "execution_closure_sha256": digest(encode(proof)),
        "runtime_binding_policy": POLICY, "runtime_roles": ["observer"],
        "required_execution_sources": sorted(installation.required_sources(
            safe(root, installation.ENTRY).read_bytes(),
        )),
        "input_sha256": proof[INPUT]["sha256"], "checked_probes": PROBES,
        "scope": "actual local ASGI/SQLite adapter and ordinary producer; not SDK or native qualification",
    }, previous.PREFIX + "protocol.py", "Actual source-bound HTTP-48 local ASGI observations.")
    shared = {"path": PRIMARY, "sha256": digest(outputs[PRIMARY])}
    for source, row in sources.items():
        if not row["semantic"]:
            continue
        packet = {
            "evidence_family": "http48-shared-asgi-v1",
            "shared_operation_evidence": shared,
            "scope": "shared named ASGI portfolio, not an independent capture",
        }
        if source not in AFFECTED:
            candidates = []
            for fixture in http["fixtures"]:
                if fixture["generation"] >= 48:
                    continue
                captured = json.loads(safe(root, fixture["path"]).read_bytes()).get(
                    "captured_from", {},
                )
                if (captured.get("source_path") == source
                        and captured.get("source_sha256") == row["sha256"]):
                    candidates.append(fixture)
            if not candidates:
                raise ValueError(f"unchanged semantic source has no prior evidence: {source}")
            old = max(candidates, key=lambda f: (f["generation"], f["path"]))
            packet = {
                "evidence_family": "http48-unchanged-source-link-v1",
                "preserved_from": {"path": old["path"], "sha256": old["sha256"]},
                "advertisement_evidence": shared,
                "scope": "unchanged source and immutable prior operation; no new runtime observation",
            }
        register(source_fixture_path(source), packet, source, (
            "Shared actual HTTP-48 ASGI evidence." if source in AFFECTED
            else "Unchanged-source link to immutable historical operation evidence."
        ))
    outputs.update(previous.registry_codec.plan(root, snapshot, registry))
    return outputs


def validate_outputs(
    root: Path, snapshot: previous.registry_codec.Snapshot, outputs: dict[str, bytes],
) -> None:
    proposed = previous.registry_codec.load(
        root, read=lambda path: outputs[path] if path in outputs
        else previous.registry_codec.read_file(root, path),
    )
    current = previous.registry_codec.load(root).materialized
    if current != snapshot.materialized and current != proposed.materialized:
        raise ValueError("HTTP-48 capture registry is dirty or belongs to another plan")
    http = contract(snapshot.materialized)
    required = {REGISTRY, PRIMARY} | {
        source_fixture_path(row["path"]) for row in http["source_paths"] if row["semantic"]
    }
    chunks = set(proposed.inputs) - {REGISTRY}
    if set(outputs) - chunks != required:
        raise ValueError("HTTP-48 output ownership inventory mismatch")
    for before, after in zip(snapshot.materialized["contracts"],
                             proposed.materialized["contracts"], strict=True):
        if before["id"] != after["id"]:
            raise ValueError("HTTP-48 changed contract ownership")
        if after["provenance"][:len(before["provenance"])] != before["provenance"]:
            raise ValueError("HTTP-48 rewrote immutable provenance")
        if len(after["fixtures"]) < len(before["fixtures"]):
            raise ValueError("HTTP-48 removed historical fixtures")
        for old, new in zip(before["fixtures"], after["fixtures"]):
            refresh = (
                before["id"] == "agent-bridge.http-wire" and old["generation"] == 48
                and old["path"] in required and new["path"] == old["path"]
                and new["generation"] == 48
            )
            if old != new and not refresh:
                raise ValueError("HTTP-48 rewrote immutable fixtures")
    immutable = (set(snapshot.inputs) - {REGISTRY}) | {
        row["path"] for owner in snapshot.materialized["contracts"] for row in owner["fixtures"]
        if not (owner["id"] == "agent-bridge.http-wire" and row["generation"] == 48
                and row["path"] in required)
    } | {
        row["reference_path"] for row in snapshot.materialized["deferred_contracts"]
        if "reference_path" in row
    }
    if immutable & outputs.keys() or len({p.casefold() for p in outputs}) != len(outputs):
        raise ValueError("HTTP-48 output collides with immutable input")
    for path, data in outputs.items():
        if len(data) > previous.registry_codec.MAX_ARTIFACT_BYTES:
            raise ValueError("HTTP-48 exceeds unchanged artifact size guard")
        target = safe(root, path)
        if target.exists() and path != REGISTRY and target.read_bytes() != data:
            registered = next((row for row in http["fixtures"] if row["path"] == path), None)
            if (registered is None or registered["generation"] != 48
                    or previous.git_evidence.sha256_bytes(target.read_bytes()) != registered["sha256"]):
                raise ValueError(f"HTTP-48 cannot replace an unowned artifact: {path}")
    loaded = {
        row["path"]: (row["sha256"], json.loads(safe(root, row["path"]).read_bytes()))
        for owner in snapshot.materialized["contracts"] for row in owner["fixtures"]
    }
    loaded.update({
        path: (digest(data), json.loads(data)) for path, data in outputs.items()
        if path in required - {REGISTRY}
    })
    validate_http48(root, proposed.materialized, loaded, errors := [], require_tree=True)
    if errors:
        raise ValueError("; ".join(errors))


def validate_http48(
    root: Path, registry: dict, loaded: dict, errors: list[str], *,
    require_tree: bool = False,
) -> None:
    try:
        primary = loaded[PRIMARY]
        packet = primary[1]
        proof = packet["execution_closure"]
        captured = packet["captured_from"]
        if (packet["evidence_family"] != FAMILY or packet["checked_probes"] != PROBES
                or packet["runtime_binding_policy"] != POLICY
                or packet["runtime_roles"] != ["observer"]
                or packet["execution_closure_sha256"] != digest(encode(proof))
                or packet["input_sha256"] != proof[INPUT]["sha256"]):
            raise ValueError("HTTP-48 primary evidence identity mismatch")
        tree_result = previous.git_evidence.git("ls-tree", "-r", captured["commit"])
        tree = {}
        if tree_result.returncode == 0:
            for line in tree_result.stdout.splitlines():
                metadata, path = line.split("\t", 1)
                mode, kind, blob = metadata.split()
                tree[path] = (mode, kind, blob)
            original = baseline(root, captured["commit"])
            required = previous.proof_paths(
                root, original.materialized,
                metadata_paths=set(original.inputs) - {REGISTRY},
            ) | set(EXTRA_PATHS)
            # Completeness belongs to the captured tree, not a future checkout.
            runtime_python = lambda p: historical.closure_python(p) or installation.closure_python(p)
            required = {p for p in required if not runtime_python(p)}
            required.update(p for p in tree if runtime_python(p))
            if set(proof) != required:
                raise ValueError("HTTP-48 closure inventory differs from captured tree")
        elif require_tree:
            raise ValueError("HTTP-48 publication requires the actual source tree")
        elif not (set(AFFECTED) | set(EXTRA_PATHS)) <= proof.keys():
            raise ValueError("HTTP-48 historical closure lacks required owners")
        previous.git_evidence.prefetch_objects(
            identity["blob"] for identity in proof.values()
            if isinstance(identity, dict) and isinstance(identity.get("blob"), str)
            and re.fullmatch(r"[0-9a-f]{40}", identity["blob"])
        )
        for path, identity in proof.items():
            safe(root, path)
            if (not isinstance(identity, dict) or set(identity) != {"blob", "sha256"}
                    or not re.fullmatch(r"[0-9a-f]{40}", identity["blob"])
                    or not re.fullmatch(r"[0-9a-f]{64}", identity["sha256"])):
                raise ValueError("HTTP-48 invalid closure identity")
            if tree and tree.get(path) not in {
                ("100644", "blob", identity["blob"]), ("100755", "blob", identity["blob"]),
            }:
                raise ValueError(f"HTTP-48 closure blob differs from tree: {path}")
            actual = previous.git_evidence.blob_sha256(identity["blob"])
            if actual is None:
                data = safe(root, path).read_bytes()
                canonical = data.decode().replace("\r\n", "\n").replace("\r", "\n").encode()
                if identity["blob"] not in {
                    hashlib.sha1(
                        b"blob " + str(len(value)).encode() + b"\0" + value,
                        usedforsecurity=False,
                    ).hexdigest() for value in (data, canonical)
                }:
                    raise ValueError(f"HTTP-48 closure blob is unprovable: {path}")
                actual = digest(canonical)
            if actual != identity["sha256"]:
                raise ValueError(f"HTTP-48 closure content mismatch: {path}")
        vector_data = safe(root, INPUT).read_bytes()
        if previous.git_evidence.sha256_bytes(vector_data) != proof[INPUT]["sha256"]:
            vector_data = previous.git(root, "cat-file", "blob", proof[INPUT]["blob"])
        if previous.git_evidence.sha256_bytes(vector_data) != proof[INPUT]["sha256"]:
            raise ValueError("HTTP-48 independent vector differs from closure")
        vector = json.loads(vector_data)
        if packet["observations"] != {
            case: vector["expected"] for case in ("legacy", "namespaced")
        }:
            raise ValueError("HTTP-48 observations differ from independent input")
        helper_data = safe(root, installation.ENTRY).read_bytes()
        helper_identity = proof[installation.ENTRY]
        if previous.git_evidence.sha256_bytes(helper_data) != helper_identity["sha256"]:
            helper_data = previous.git(root, "cat-file", "blob", helper_identity["blob"])
        if previous.git_evidence.sha256_bytes(helper_data) != helper_identity["sha256"]:
            raise ValueError("HTTP-48 installation helper differs from closure")
        required_execution = installation.required_sources(helper_data)
        if (not required_execution <= proof.keys()
                or packet["required_execution_sources"] != sorted(required_execution)):
            raise ValueError("HTTP-48 installation execution inventory is incomplete")
        shared = {"path": PRIMARY, "sha256": primary[0]}
        if any(captured[key] != proof[captured["source_path"]][name] for key, name in (
            ("source_git_blob", "blob"), ("source_sha256", "sha256"),
        )):
            raise ValueError("HTTP-48 primary source differs from closure")
        for source in AFFECTED:
            row = next(r for r in contract(registry)["source_paths"] if r["path"] == source)
            child = loaded[source_fixture_path(source)][1]
            if (not row["semantic"] or row["sha256"] != proof[source]["sha256"]
                    or child["shared_operation_evidence"] != shared):
                raise ValueError(f"HTTP-48 affected semantic source is not bound: {source}")
            origin = child["captured_from"]
            if (origin["source_path"] != source or origin["commit"] != captured["commit"]
                    or origin["source_git_blob"] != proof[source]["blob"]
                    or origin["source_sha256"] != proof[source]["sha256"]):
                raise ValueError(f"HTTP-48 shared source provenance mismatch: {source}")
        for row in contract(registry)["source_paths"]:
            source = row["path"]
            if not row["semantic"] or source in AFFECTED:
                continue
            child = loaded[source_fixture_path(source)][1]
            prior = child["preserved_from"]
            before = loaded[prior["path"]]
            origin, old = child["captured_from"], before[1]["captured_from"]
            if (child["evidence_family"] != "http48-unchanged-source-link-v1"
                    or child["advertisement_evidence"] != shared
                    or prior["sha256"] != before[0]
                    or origin["source_path"] != source or old["source_path"] != source
                    or origin["source_sha256"] != proof[source]["sha256"]
                    or old["source_sha256"] != origin["source_sha256"]
                    or old["protocol_generation"] >= 48
                    or origin["commit"] != captured["commit"]
                    or origin["source_git_blob"] != proof[source]["blob"]):
                raise ValueError(f"HTTP-48 unchanged-source link mismatch: {source}")
    except (ValueError, KeyError, TypeError, OSError, StopIteration) as exc:
        errors.append(f"HTTP-48 execution closure: {exc}")
