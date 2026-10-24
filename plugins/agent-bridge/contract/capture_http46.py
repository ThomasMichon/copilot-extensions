"""Reproduce bounded HTTP-46 source evidence without rewriting historical packets."""
from __future__ import annotations

import argparse
import copy
import json
from pathlib import Path
import re
import stat
import sys

ROOT = Path(__file__).resolve().parents[3]
sys.path.insert(0, str(ROOT / "tools"))
sys.path.insert(0, str(ROOT / "libs/agent-procutil/src"))
import agent_bridge_contract_git as evidence  # noqa: E402
import agent_bridge_contract_registry as registry_codec  # noqa: E402
from agent_bridge_contract_publication import publish  # noqa: E402
from agent_procutil.owned_process import run_caller_owned  # noqa: E402

CONTRACT = "plugins/agent-bridge/contract/"
REGISTRY = CONTRACT + "registry.json"
CURRENT = CONTRACT + "fixtures/http/current/"
PREFIX = "plugins/agent-bridge/src/agent_bridge/"
PRIMARY = CURRENT + "startup-observation-generation-46.json"
STARTUP_SOURCES = (
    "protocol.py", "client.py", "client_prompt.py", "session_resume.py",
    "session_targeting_cli.py", "startup_observation.py", "startup_progress.py",
    "send_outcome.py", "__main__.py",
)
FORWARDERS = ("connect.py", "session_host_connection.py", "transport.py")


def safe(relative):
    target = ROOT / relative
    if not target.resolve().is_relative_to(ROOT.resolve()):
        raise ValueError(f"capture path escapes repository: {relative}")
    for node in (target, *target.parents):
        if node.is_symlink() or getattr(node, "is_junction", lambda: False)():
            raise ValueError(f"linked capture path refused: {relative}")
        if node.exists() and getattr(node.lstat(), "st_file_attributes", 0) & getattr(
            stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400,
        ):
            raise ValueError(f"reparse capture path refused: {relative}")
        if node == ROOT:
            break
    if target.exists() and (not target.is_file() or target.stat().st_nlink != 1):
        raise ValueError(f"aliased or non-file capture path refused: {relative}")
    return target


def read(relative):
    return json.loads(safe(relative).read_text(encoding="utf-8"))


def encode(value):
    return (json.dumps(value, indent=2, ensure_ascii=False) + "\n").encode("utf-8")


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--source-commit", required=True)
    parser.add_argument("--write", action="store_true")
    args = parser.parse_args()
    commit = args.source_commit
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("an actual full source commit is required")
    if evidence.integer_constant_at(commit, PREFIX + "protocol.py", "HTTP_PROTOCOL_VERSION") != 46:
        raise ValueError("selected source is not HTTP-46")
    if args.write:
        from agent_bridge_contract_publication import recover_pending_links
        recover_pending_links(ROOT, commit)
    snapshot = registry_codec.load(ROOT)
    registry = copy.deepcopy(snapshot.materialized)
    http = next(row for row in registry["contracts"] if row["id"] == "agent-bridge.http-wire")
    if http["declared_range"]["current"] not in (45, 46):
        raise ValueError("this capture owns only the HTTP-45 to HTTP-46 transition")
    old_rows = copy.deepcopy([row for row in http["fixtures"] if row["generation"] < 46])
    old_capabilities = {key: value for key, value in http["capability_versions"].items()
                        if key != "startup_progress"}
    for row in old_rows:
        if evidence.sha256_bytes(safe(row["path"]).read_bytes()) != row["sha256"]:
            raise ValueError(f"historical evidence changed: {row['path']}")
    source_rows = {row["path"]: row for row in http["source_paths"]}
    for name in STARTUP_SOURCES:
        path = PREFIX + name
        if path not in source_rows:
            row = {"path": path, "sha256": "", "semantic": True, "non_semantic_reason": None}
            http["source_paths"].append(row)
            source_rows[path] = row
        source_rows[path].update(semantic=True, non_semantic_reason=None)
    for name in FORWARDERS:
        path = PREFIX + name
        if path not in source_rows:
            row = {
                "path": path, "sha256": "", "semantic": False,
                "non_semantic_reason": (
                    "Startup instrumentation forwards preparation/connection activity to the "
                    "registered startup_progress producer; it owns no separate HTTP packet, "
                    "capability floor, admission decision, cancellation or retry. The shared "
                    "HTTP-46 startup operation and existing progress tests cover the forwarding."
                ),
            }
            http["source_paths"].append(row)
            source_rows[path] = row
    version = read("plugins/agent-bridge/plugin.json")["version"]
    if evidence.plugin_version_at(commit) != version:
        raise ValueError("plugin version differs from selected source")
    proofs = {}
    for path, row in source_rows.items():
        sha = evidence.sha256_bytes(safe(path).read_bytes())
        if evidence.git_file_sha256(commit, path) != sha:
            raise ValueError(f"dirty or unproven source: {path}")
        blob = evidence.git_blob(commit, path)
        if blob is None:
            raise ValueError(f"source blob unavailable: {path}")
        proofs[path] = {
            "commit": commit, "plugin_version": version, "source_path": path,
            "source_git_blob": blob, "protocol_generation": 46, "source_sha256": sha,
        }
        row["sha256"] = sha
    outputs = {}
    methods = {}

    def register(path, value, source, method):
        value = copy.deepcopy(value)
        value["captured_from"] = proofs[source]
        outputs[path] = encode(value)
        row = {
            "path": path, "sha256": evidence.sha256_bytes(outputs[path]),
            "role": method, "generation": 46,
        }
        existing = next((item for item in http["fixtures"] if item["path"] == path), None)
        if existing is None:
            http["fixtures"].append(row)
        else:
            existing.update(row)
        methods.setdefault(source, set()).add(method)

    linked = (
        "Linked same-source HTTP-45 family re-attestation against committed HTTP-46 source; "
        "all predecessor packet bytes/advertisements/floors remain frozen. Not an independent "
        "HTTP-46 runtime capture; current advertisement and new behavior have separate operation evidence."
    )
    for row in old_rows:
        if row["generation"] != 45:
            continue
        value = read(row["path"])
        source = value["captured_from"]["source_path"]
        value["reattested_from"] = {"path": row["path"], "sha256": row["sha256"]}
        path = row["path"].removesuffix("-generation-45.json") + "-generation-46.json"
        register(path, value, source, linked)
    input_path = CONTRACT + "inputs/http46/startup-observation.json"
    input_sha = evidence.sha256_bytes(safe(input_path).read_bytes())
    vector = read(input_path)
    independent = (
        "Independently authored HTTP-46 startup operation expectations replayed through the "
        "production parser, progress producer/observer, client GET preflight and PromptClientMixin, "
        "and JSON/non-JSON send renderer. Synthetic peer/clock; no daemon or model execution."
    )
    register(PRIMARY, vector, PREFIX + "startup_observation.py", independent)
    shared = {
        "path": PRIMARY, "sha256": evidence.sha256_bytes(outputs[PRIMARY]),
    }
    for name in STARTUP_SOURCES:
        if name == "startup_observation.py":
            continue
        register(
            CURRENT + "startup-shared-" + name.removesuffix(".py") + "-generation-46.json",
            {**vector, "shared_operation_evidence": shared}, PREFIX + name,
            "Shared new startup operation evidence, not an independent capture. " + independent,
        )
    for source, method in methods.items():
        proof = proofs[source]
        provenance = {
            "commit": commit, "plugin_version": version, "generation": 46,
            "source_path": source, "source_git_blob": proof["source_git_blob"],
            "capture_method": " ".join(sorted(method)),
        }
        if provenance not in http["provenance"]:
            http["provenance"].append(provenance)
    http["declared_range"].update(current=46, previous_generation=45)
    if 46 not in http["evidence_window"]["generations"]:
        http["evidence_window"]["generations"].append(46)
    if version not in http["evidence_window"]["runtimes"]:
        http["evidence_window"]["runtimes"].append(version)
    http["capability_versions"]["startup_progress"] = 46
    for name in ("startup-old-peer-refusal-json-and-text", "startup-lost-ack-no-replay",
                 "startup-preflight-late-response", "startup-ready-ends-only-startup-budget"):
        if name not in http["mixed_version_scenarios"]:
            http["mixed_version_scenarios"].append(name)
    if old_capabilities != {key: value for key, value in http["capability_versions"].items()
                            if key != "startup_progress"}:
        raise ValueError("a historical capability floor changed")
    covered = {
        json.loads(raw)["captured_from"]["source_path"] for raw in outputs.values()
    }
    if any(row["semantic"] and path not in covered for path, row in source_rows.items()):
        raise ValueError("semantic source lacks current evidence")
    outputs.update(registry_codec.plan(ROOT, snapshot, registry))
    for path in outputs:
        safe(path)
    # Replay authored expectations before writing any proposed registry/fixture.
    result = run_caller_owned(
        [sys.executable, str(ROOT / "tools/run-plugin-tests.py"), "agent-bridge",
         "--allow-explicit-tiers", "--test-file", "tests/test_http46_startup_evidence.py",
         "-k", "not generation46_attestations"],
        timeout=180, max_stdout_bytes=4 * 1024 * 1024, max_stderr_bytes=1024 * 1024,
    )
    print(result.stdout or "", end="")
    print(result.stderr or "", end="", file=sys.stderr)
    if result.returncode:
        raise ValueError("bounded operation replay failed; no capture outputs written")
    if evidence.sha256_bytes(safe(input_path).read_bytes()) != input_sha:
        raise ValueError("independent input changed during replay; no outputs written")
    for path, proof in proofs.items():
        if evidence.sha256_bytes(safe(path).read_bytes()) != proof["source_sha256"]:
            raise ValueError(f"source changed during replay: {path}")
    for row in old_rows:
        if evidence.sha256_bytes(safe(row["path"]).read_bytes()) != row["sha256"]:
            raise ValueError(f"historical evidence changed during replay: {row['path']}")
    snapshot.verify(ROOT)
    if args.write:
        publish(
            ROOT, commit, outputs, validate=lambda: snapshot.verify(ROOT),
            input_hashes={
                row["path"]: registry_codec.digest(safe(row["path"]).read_bytes())
                for row in old_rows
            },
        )
    for path, raw in outputs.items():
        target = safe(path)
        if not target.exists() or not evidence.sha256_bytes(target.read_bytes()) == evidence.sha256_bytes(raw):
            raise ValueError(f"capture is not reproducible: {path}")
    print(f"HTTP-46 evidence: {len(outputs) - 1} fixtures; source {commit}; "
          f"registry SHA-256 {evidence.sha256_bytes(outputs[REGISTRY])}")


if __name__ == "__main__":
    main()
