"""Committed-source and confined-output rules for the HTTP-47 capture only."""

from __future__ import annotations

import ast
import copy
import hashlib
import json
from pathlib import Path
import re
import subprocess
import sys
from typing import Any

import agent_bridge_contract_git as git_evidence
import agent_bridge_contract_registry as registry_codec
from agent_bridge_contract_paths import safe

CONTRACT = "plugins/agent-bridge/contract/"
REGISTRY = CONTRACT + "registry.json"
INPUT = CONTRACT + "inputs/http47/capture-cases.json"
CONTROL_INPUT = CONTRACT + "inputs/http47/negotiated-drain.json"
CURRENT = CONTRACT + "fixtures/http/current/"
PRIMARY = CURRENT + "http47-owned-cold-drain.json"
PREFIX = "plugins/agent-bridge/src/agent_bridge/"
AFFECTED = tuple(
    PREFIX + name
    for name in (
        "protocol.py",
        "drain_contract.py",
        "client.py",
        "client_drain.py",
        "routes/admin.py",
        "routes/health.py",
        "routes/sessions.py",
        "cold_outages.py",
        "session_cold_drain.py",
        "db_core.py",
        "db_schema.py",
        "db_sessions.py",
        "session_core.py",
        "session_drain_gate.py",
        "session_handoff.py",
        "session_host_recovery.py",
        "session_host_connection.py",
        "session_lifecycle.py",
        "session_recovery_dormancy.py",
        "session_resume.py",
        "session_prompts.py",
        "app.py",
        "app_supersession.py",
        "session_host/launcher.py",
    )
) + (
    "libs/zdd/src/zdd/cutover_drain.py",
    "libs/zdd/src/zdd/conditional.py",
    "plugins/agent-worktrees/libs/zdd/src/zdd/conditional.py",
)
TESTS = (
    "plugins/agent-bridge/tests/conftest.py",
    "plugins/agent-bridge/tests/http47_binding_validation.py",
    "plugins/agent-bridge/tests/test_http47_binding_portfolio.py",
    "plugins/agent-bridge/tests/test_http47_capture_probes.py",
    "plugins/agent-bridge/tests/test_cold_drain.py",
    "plugins/agent-bridge/tests/test_cold_recovery_races.py",
    "plugins/agent-bridge/tests/test_cold_recovery_drain.py",
    "plugins/agent-bridge/tests/test_cold_recovery_handoff_fence.py",
    "plugins/agent-bridge/tests/test_cold_recovery_stop.py",
    "plugins/agent-bridge/tests/test_cold_recovery_launch_cancellation.py",
    "plugins/agent-bridge/tests/test_db_adoption.py",
    "plugins/agent-bridge/tests/test_db_custody_schema.py",
    "plugins/agent-bridge/tests/cold_recovery_worker.py",
    "plugins/agent-bridge/tests/cold_drain_peer.py",
)
PROBES = {
    "test_http47_capture_actual_observations": 1,
    "test_cold_drain_default_client_automatic_and_strict_cutover_precedes_effects": 1,
    "test_cold_drain_strict_graceful_rejects_even_mislabelled_clean": 2,
    "test_startup_cold_outage_after_initial_scan_waits_for_predecessor_exit": 1,
    "test_heartbeat_partial_cold_replacement_exit_preserves_delivery_fence": 2,
    "test_cold_recovery_two_successors_exclude_before_native_spawn": 1,
    "test_cold_recovery_registration_crash_adopts_existing_host": 1,
    "test_cold_recovery_prepared_and_failed_reconcile_only_exact_exit": 4,
    "test_cold_recovery_unknown_admission_or_launch_is_never_replayed": 8,
    "test_cold_recovery_provider_failure_is_not_confirmation": 1,
    "test_cold_recovery_resync_routes_same_native_without_rebuild": 1,
    "test_cold_recovery_closed_drain_has_no_launch_or_unknown_delivery": 1,
    "test_cold_recovery_entered_before_drain_finishes_same_native_reconnect": 1,
    "test_cold_recovery_admission_is_counted_by_destructive_drain": 1,
    "test_cold_recovery_cancellation_releases_drain_accounting": 1,
    "test_cold_recovery_drain_exception_requires_exact_task_and_kernel_custody": 1,
    "test_cold_handoff_refuses_unconfirmed_outage": 15,
    "test_cold_handoff_turn_settle_cannot_escape_unknown_reconnect": 1,
    "test_cold_handoff_allows_ordinary_and_confirmed_outage": 2,
    "test_cold_handoff_rechecks_outage_after_awaited_brief": 1,
    "test_cold_handoff_rechecks_outage_after_awaited_resume": 1,
    "test_cold_drain_client_rejects_nonobject_outcome": 10,
    "test_cold_drain_client_requires_boolean_completion_flags": 21,
    "test_cold_drain_client_does_not_accept_a_legacy_success_shape": 1,
    "test_ordinary_recovery_closed_cold_gate_is_503_without_effects": 2,
    "test_ordinary_recovery_entered_before_drain_is_counted": 4,
    "test_ordinary_recovery_cancellation_releases_drain_accounting": 2,
    "test_cancelled_real_launcher_thread_remains_in_drain_census": 3,
    "test_cold_stop_migration_preserves_ambiguous_legacy_dormancy": 1,
    "test_cold_stop_schema_current_version_missing_column_is_ensured": 1,
    "test_cold_stop_fences_startup_and_heartbeat_after_rehydration": 2,
    "test_cold_stop_manual_resume_preserves_native_and_clears_captured_token": 1,
    "test_cold_stop_during_admission_prevents_native_launch": 3,
    "test_cold_stop_during_launch_preparation_prevents_native_spawn": 6,
    "test_cold_stop_after_native_launch_prevents_reconnect": 1,
    "test_cold_stop_unknown_reconnect_is_not_replayed_by_manual_resume": 1,
    "test_cold_stop_provider_completion_cannot_reenable_recovery": 1,
    "test_cold_stop_busy_refusal_has_no_durable_intent": 1,
    "test_cold_stop_redeploy_detach_does_not_cancel_recovery": 1,
    "test_cold_stop_inherited_automatic_origin_cannot_become_manual": 1,
    "test_cold_stop_token_compare_and_set_cannot_clear_a_newer_stop": 1,
    "test_cold_stop_two_owner_conditional_enable_preserves_dormancy": 2,
    "test_cold_stop_setter_returns_actual_enabled_state_without_stop": 2,
    "test_cold_stop_unsettled_launches_include_orphans_and_historical_rows": 1,
}
FAMILY = "http47-owned-fixture-observations-v1"


def encode(value: Any, *, indent: int | None = 2) -> bytes:
    return (
        json.dumps(
            value, indent=indent, ensure_ascii=False, allow_nan=False,
            separators=(",", ":") if indent is None else None,
        ) + "\n"
    ).encode("utf-8")


def digest(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def git(root: Path, *args: str) -> bytes:
    git_evidence.require_local_object_reads()
    result = subprocess.run(
        ["git", "-c", f"safe.directory={root}", "-C", str(root), *args],
        capture_output=True,
        check=False,
        timeout=15,
        env=git_evidence.clean_git_environment(),
        creationflags=subprocess.CREATE_NO_WINDOW if sys.platform == "win32" else 0,
    )
    if result.returncode:
        raise ValueError(f"local capture Git evidence unavailable: {args[0]}")
    return result.stdout


def constant(data: bytes, name: str) -> int:
    for node in ast.parse(data.decode("utf-8")).body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
            and type(node.value.value) is int
        ):
            return node.value.value
    raise ValueError(f"missing integer capture constant: {name}")


def proof_paths(
    root: Path, registry: dict, *, metadata_paths: set[str] | None = None,
) -> set[str]:
    """Pin execution dependencies broadly; only AFFECTED owns new semantic vectors."""
    paths = (
        set(AFFECTED)
        | set(TESTS)
        | {
            INPUT,
            CONTROL_INPUT,
            "plugins/agent-bridge/plugin.json",
            "plugins/agent-bridge/pyproject.toml",
            CONTRACT + "registry.schema.json",
        }
    )
    if (root / (CONTRACT + "provenance.schema.json")).exists():
        paths.add(CONTRACT + "provenance.schema.json")
    paths.update(metadata_paths or set())
    for contract in registry["contracts"]:
        paths.update(item["path"] for item in contract["source_paths"])
    for folder in (
        root / "plugins/agent-bridge/src",
        *sorted((root / "libs").glob("*/src")),
    ):
        if folder.exists():
            paths.update(
                p.relative_to(root).as_posix()
                for p in folder.rglob("*.py")
                if "__pycache__" not in p.parts
            )
    paths.update(p.relative_to(root).as_posix() for p in (root / "tools").glob("*.py"))
    # The runner's local package closure is also source, not an installed-tool fallback.
    for folder in (
        root / "tools/plugin_test_containment",
        root / "tools/seasoned_uv",
        root / "tools/http47_capture_bootstrap",
        root / "libs/installer-engine",
    ):
        if folder.exists():
            paths.update(
                p.relative_to(root).as_posix()
                for p in folder.rglob("*.py")
                if "tests" not in p.relative_to(folder).parts
            )
    return paths


def source_snapshot(
    root: Path, commit: str, registry: dict
) -> dict[str, dict[str, str]]:
    tree = {}
    for raw in git(root, "ls-tree", "-rz", commit).split(b"\0"):
        if not raw:
            continue
        metadata, path = raw.split(b"\t", 1)
        mode, kind, blob = metadata.decode().split()
        tree[path.decode("utf-8")] = (mode, kind, blob)
    result = {}
    metadata = baseline_snapshot(root, commit)
    for path in sorted(proof_paths(
        root, registry, metadata_paths=set(metadata.inputs) - {REGISTRY},
    )):
        node = safe(root, path)
        entry = tree.get(path)
        if entry is None or entry[:2] not in {("100644", "blob"), ("100755", "blob")}:
            raise ValueError(f"capture source is not a committed plain blob: {path}")
        data = node.read_bytes()
        canonical = (
            data.decode("utf-8").replace("\r\n", "\n").replace("\r", "\n").encode()
        )
        blobs = {
            hashlib.sha1(
                b"blob " + str(len(value)).encode() + b"\0" + value,
                usedforsecurity=False,
            ).hexdigest()
            for value in (data, canonical)
        }
        if entry[2] not in blobs:
            raise ValueError(f"dirty/unproven capture source: {path}")
        result[path] = {"blob": entry[2], "sha256": digest(canonical)}
    return result


def baseline_snapshot(root: Path, commit: str) -> registry_codec.Snapshot:
    if not re.fullmatch(r"[0-9a-f]{40}", commit):
        raise ValueError("HTTP-47 capture requires a full real source commit")
    if (
        git(root, "rev-parse", "--verify", commit + "^{commit}").decode().strip()
        != commit
    ):
        raise ValueError("capture source is not the selected commit")
    if (
        constant(
            git(root, "show", commit + ":" + PREFIX + "protocol.py"),
            "HTTP_PROTOCOL_VERSION",
        )
        != 47
    ):
        raise ValueError(
            "capture source commit must genuinely speak HTTP generation 47"
        )
    if (
        constant(
            git(root, "show", commit + ":" + PREFIX + "protocol.py"),
            "NEGOTIATED_DRAIN_PROTOCOL_VERSION",
        )
        != 47
    ):
        raise ValueError(
            "HTTP-47 capture cannot move the negotiated-drain capability floor"
        )
    snapshot = registry_codec.load(
        root, read=lambda path: git(root, "show", commit + ":" + path),
    )
    result = snapshot.materialized
    http = next(c for c in result["contracts"] if c["id"] == "agent-bridge.http-wire")
    if http["declared_range"]["current"] != 47:
        raise ValueError("this capture owns only declared HTTP generation 47")
    return snapshot


def baseline(root: Path, commit: str) -> dict:
    return baseline_snapshot(root, commit).materialized


def source_fixture_path(source: str) -> str:
    stem = source.rsplit("/", 1)[-1].replace(".", "-")
    return (
        CURRENT + "http47-source-" + stem + "-" + digest(source.encode())[:12] + ".json"
    )


def output_paths(registry: dict) -> set[str]:
    http = next(c for c in registry["contracts"] if c["id"] == "agent-bridge.http-wire")
    semantic = {s["path"] for s in http["source_paths"] if s["semantic"]} | set(
        AFFECTED
    )
    return {REGISTRY, PRIMARY} | {source_fixture_path(source) for source in semantic}


def check_history(
    root: Path, registry: dict, outputs: dict[str, bytes] | None = None
) -> dict[str, str | None]:
    owned = output_paths(registry) - {REGISTRY} if outputs is not None else set()
    if outputs is not None:
        validate_outputs(root, registry, outputs)
    snapshot: dict[str, str | None] = {}
    metadata = registry_codec.load(root)
    snapshot.update({path: digest(data) for path, data in metadata.inputs.items()})
    for contract in registry["contracts"]:
        for fixture in contract["fixtures"]:
            data = safe(root, fixture["path"]).read_bytes()
            refreshable = (
                contract["id"] == "agent-bridge.http-wire"
                and type(fixture["generation"]) is int
                and fixture["generation"] == 47 and fixture["path"] in owned
            )
            if not refreshable and git_evidence.sha256_bytes(data) != fixture["sha256"]:
                raise ValueError(
                    f"historical/current baseline fixture drift: {fixture['path']}"
                )
            snapshot[fixture["path"]] = digest(data)
    for reference in registry["deferred_contracts"]:
        if "reference_path" not in reference:
            continue
        path = reference["reference_path"]
        data = safe(root, path).read_bytes()
        if git_evidence.sha256_bytes(data) != reference["reference_sha256"]:
            raise ValueError(f"deferred owner reference drift: {path}")
        snapshot[path] = digest(data)
    if outputs is not None:
        for path in outputs:
            target = safe(root, path)
            snapshot[path] = digest(target.read_bytes()) if target.is_file() else None
    return snapshot


def build_outputs(
    root: Path, commit: str, original: dict, proof: dict, observed: dict
) -> dict[str, bytes]:
    registry = copy.deepcopy(original)
    http = next(c for c in registry["contracts"] if c["id"] == "agent-bridge.http-wire")
    version = json.loads(safe(root, "plugins/agent-bridge/plugin.json").read_bytes())[
        "version"
    ]
    if 47 not in http["evidence_window"]["generations"]:
        http["evidence_window"]["generations"].append(47)
    if version not in http["evidence_window"]["runtimes"]:
        http["evidence_window"]["runtimes"].append(version)
    sources = {item["path"]: item for item in http["source_paths"]}
    for source in AFFECTED:
        if source not in sources:
            sources[source] = {"path": source}
            http["source_paths"].append(sources[source])
        sources[source].update(
            sha256=proof[source]["sha256"], semantic=True, non_semantic_reason=None
        )
    outputs = {}

    def register(path: str, value: dict, source: str, method: str) -> None:
        if path in outputs:
            raise ValueError(f"capture output collision: {path}")
        value = copy.deepcopy(value)
        value["captured_from"] = {
            "commit": commit,
            "plugin_version": version,
            "protocol_generation": 47,
            "source_path": source,
            "source_git_blob": proof[source]["blob"],
            "source_sha256": proof[source]["sha256"],
        }
        data = encode(value)
        outputs[path] = data
        old = next((f for f in http["fixtures"] if f["path"] == path), None)
        record = {
            "path": path,
            "sha256": digest(data),
            "role": method,
            "generation": 47,
        }
        if old is None:
            http["fixtures"].append(record)
        else:
            if old["generation"] != 47:
                raise ValueError("capture output would replace historical evidence")
            old.update(record)
        provenance = {
            "commit": commit,
            "plugin_version": version,
            "generation": 47,
            "source_path": source,
            "source_git_blob": proof[source]["blob"],
            "capture_method": method,
        }
        if provenance not in http["provenance"]:
            http["provenance"].append(provenance)

    register(
        PRIMARY,
        {
            "evidence_family": FAMILY,
            "observations": observed,
            "execution_closure_sha256": digest(encode(proof)),
            "execution_closure": proof,
            "runtime_binding_policy": "every-owned-interpreter-source-and-entrypoint-v1",
            "input_sha256": proof[INPUT]["sha256"],
            "checked_probes": PROBES,
            "scope": "model-free ASGI and owned stdio fixtures; not real Copilot, deployment or host qualification",
        },
        PREFIX + "protocol.py",
        "Real ASGI/owned-process HTTP-47 observations and bounded native-fixture regressions.",
    )
    shared = {"path": PRIMARY, "sha256": digest(outputs[PRIMARY])}
    for source, row in sources.items():
        if source not in proof:
            raise ValueError(f"unproven registry source: {source}")
        if source in AFFECTED:
            register(
                source_fixture_path(source),
                {
                    "evidence_family": "http47-shared-owned-fixture-v1",
                    "shared_operation_evidence": shared,
                    "scope": "shared observations exercised by the named probe portfolio, not an independent capture",
                },
                source,
                "Shared actual HTTP-47 operation/owned-process evidence for the affected semantic closure.",
            )
        elif row["sha256"] != proof[source]["sha256"]:
            raise ValueError(
                f"unclassified source drift outside HTTP-47 closure: {source}"
            )
        elif row["semantic"]:
            candidates = []
            for fixture in http["fixtures"]:
                if fixture["generation"] >= 47:
                    continue
                value = json.loads(safe(root, fixture["path"]).read_bytes())
                captured = value.get("captured_from", {})
                if (
                    captured.get("source_path") == source
                    and captured.get("source_sha256") == row["sha256"]
                ):
                    candidates.append(fixture)
            if not candidates:
                raise ValueError(
                    f"unchanged semantic source has no matching historical vector: {source}"
                )
            previous = sorted(candidates, key=lambda f: (f["generation"], f["path"]))[
                -1
            ]
            register(
                source_fixture_path(source),
                {
                    "evidence_family": "http47-unchanged-source-link-v1",
                    "preserved_from": {
                        "path": previous["path"],
                        "sha256": previous["sha256"],
                    },
                    "advertisement_evidence": shared,
                    "scope": "source bytes unchanged; previous-generation vector preserved, not a new runtime capture",
                },
                source,
                "Unchanged-source reattestation linked to immutable prior evidence; no copied or relabelled runtime packet.",
            )
    snapshot = baseline_snapshot(root, commit)
    if snapshot.materialized != original:
        raise ValueError("capture baseline differs from the committed registry")
    outputs.update(registry_codec.plan(root, snapshot, registry))
    return outputs


def validate_outputs(root: Path, original: dict, outputs: dict[str, bytes]) -> None:
    plan = output_paths(original)
    proposed = registry_codec.load(
        root, read=lambda path: outputs[path] if path in outputs
        else registry_codec.read_file(root, path),
    )
    chunks = set(proposed.inputs) - {REGISTRY}
    if set(outputs) - chunks != plan:
        raise ValueError("capture output plan differs from committed ownership")
    old_contracts = {c["id"]: c for c in original["contracts"]}
    for contract in proposed.materialized["contracts"]:
        before = old_contracts[contract["id"]]["provenance"]
        if contract["provenance"][:len(before)] != before:
            raise ValueError("capture changed historical provenance")
    packets = plan - {REGISTRY}
    immutable = {
        f["path"]
        for c in original["contracts"]
        for f in c["fixtures"]
        if not (
            c["id"] == "agent-bridge.http-wire"
            and type(f["generation"]) is int and f["generation"] == 47
            and f["path"] in packets
        )
    } | {
        ref["reference_path"] for ref in original["deferred_contracts"]
        if "reference_path" in ref
    }
    if (
        len({p.casefold() for p in outputs}) != len(outputs)
        or {p.casefold() for p in immutable} & {p.casefold() for p in outputs}
    ):
        raise ValueError("capture output collision with another path/immutable reference")
    registered = {
        f["path"]: f["sha256"] for c in original["contracts"] for f in c["fixtures"]
    }
    for relative in outputs:
        if len(outputs[relative]) > registry_codec.MAX_ARTIFACT_BYTES:
            raise ValueError(f"capture output exceeds the unchanged size guard: {relative}")
        target = safe(root, relative)
        if target.exists() and not target.is_file():
            raise ValueError(f"capture output is not a plain file: {relative}")
        if relative == REGISTRY:
            current = registry_codec.load(root).materialized
            current_key = json.dumps(current, sort_keys=True, allow_nan=False)
            allowed = (
                json.dumps(original, sort_keys=True, allow_nan=False),
                json.dumps(
                    proposed.materialized, sort_keys=True, allow_nan=False
                ),
            )
            if current_key not in allowed:
                raise ValueError("dirty capture registry output")
        if relative in chunks:
            if target.exists() and target.read_bytes() != outputs[relative]:
                raise ValueError(f"immutable provenance output changed: {relative}")
            continue
        if relative != REGISTRY and target.exists():
            existing = json.loads(target.read_bytes())
            planned = json.loads(outputs[relative])
            source = planned.get("captured_from", {}).get("source_path")
            semantic_upgrade = (
                source in AFFECTED and relative == source_fixture_path(source)
                and existing.get("evidence_family") == "http47-unchanged-source-link-v1"
                and planned.get("evidence_family") == "http47-shared-owned-fixture-v1"
                and git_evidence.sha256_bytes(target.read_bytes()) == registered.get(relative)
            )
            if (
                existing.get("evidence_family")
                not in {
                    FAMILY,
                    "http47-shared-owned-fixture-v1",
                    "http47-unchanged-source-link-v1",
                }
                or existing.get("captured_from", {}).get("protocol_generation") != 47
                or (
                    existing.get("evidence_family") != planned["evidence_family"]
                    and not semantic_upgrade
                )
                or existing.get("captured_from", {}).get("source_path")
                != planned["captured_from"]["source_path"]
            ):
                raise ValueError(f"unowned capture output: {relative}")
            actual = git_evidence.sha256_bytes(target.read_bytes())
            if actual != digest(outputs[relative]) and actual != registered.get(
                relative
            ):
                raise ValueError(f"dirty capture output: {relative}")
