#!/usr/bin/env python3
"""Validate the immutable agent-bridge contract registry and evidence corpus.

The registry is intentionally dependency-free repository evidence. This checker
validates its structure, path confinement, hashes, source provenance, production
protocol constants, and optional diff-scoped source coverage.

Usage:

    python tools/check-agent-bridge-contracts.py
    python tools/check-agent-bridge-contracts.py --base <sha>
"""
from __future__ import annotations

import argparse
import ast
import hashlib
import json
import os
import re
import subprocess
import sys
from pathlib import Path
from typing import Any

REPO = Path(__file__).resolve().parent.parent
CONTRACT_DIR = REPO / "plugins" / "agent-bridge" / "contract"
REGISTRY_PATH = CONTRACT_DIR / "registry.json"
SCHEMA_PATH = CONTRACT_DIR / "registry.schema.json"

_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_GIT_OBJECT_RE = re.compile(r"^[0-9a-f]{40}$")
_ID_RE = re.compile(r"^[a-z0-9][a-z0-9.-]+$")
_REQUIRED_PROTOCOL_CONTRACTS = {
    "agent-bridge.http-wire",
    "agent-bridge.session-host-wire",
}
_CONTRACT_KEYS = {
    "id",
    "authority",
    "owner",
    "kind",
    "normative",
    "declared_range",
    "evidence_window",
    "capability_versions",
    "durable_records",
    "source_paths",
    "fixtures",
    "provenance",
    "support_window",
    "bridge_contract_rollback_window",
    "mixed_version_scenarios",
    "removal_gate",
}
_DEFERRED_KEYS = {
    "id",
    "owner",
    "classification",
    "tracked_issue",
    "reason",
    "reference_path",
    "reference_sha256",
}
_HTTP_CAPABILITY_CONSTANTS = {
    "relay_interrupt": "RELAY_INTERRUPT_PROTOCOL_VERSION",
    "failed_acp_handshake": "FAILED_ACP_HANDSHAKE_PROTOCOL_VERSION",
    "container_recreate": "CONTAINER_RECREATE_PROTOCOL_VERSION",
    "machine_metadata": "MACHINE_METADATA_PROTOCOL_VERSION",
    "result_snapshot": "RESULT_SNAPSHOT_PROTOCOL_VERSION",
    "represented_result_snapshot": "REPRESENTED_RESULT_SNAPSHOT_PROTOCOL_VERSION",
    "provider_target_refresh": "PROVIDER_TARGET_REFRESH_PROTOCOL_VERSION",
    "at_rest_projection": "AT_REST_PROJECTION_PROTOCOL_VERSION",
    "attention_wait": "ATTENTION_WAIT_PROTOCOL_VERSION",
    "remote_operations": "REMOTE_OPERATIONS_PROTOCOL_VERSION",
    "conditional_idle_end": "CONDITIONAL_IDLE_END_PROTOCOL_VERSION",
}


def _clean_git_environment() -> dict[str, str]:
    environment = {
        key: value for key, value in os.environ.items() if not key.startswith("GIT_")
    }
    environment.update(
        {
            "GIT_CONFIG_GLOBAL": os.devnull,
            "GIT_CONFIG_NOSYSTEM": "1",
            "GIT_NO_LAZY_FETCH": "1",
            "GIT_NO_REPLACE_OBJECTS": "1",
            "GIT_OPTIONAL_LOCKS": "0",
            "GIT_TERMINAL_PROMPT": "0",
        }
    )
    return environment


def _load_json(path: Path, errors: list[str], label: str) -> Any | None:
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except FileNotFoundError:
        errors.append(f"{label}: missing file {path.relative_to(REPO).as_posix()}")
    except (OSError, json.JSONDecodeError) as exc:
        errors.append(f"{label}: cannot read valid JSON: {exc}")
    return None


def _sha256_bytes(data: bytes) -> str:
    try:
        text = data.decode("utf-8")
    except UnicodeDecodeError:
        canonical = data
    else:
        canonical = text.replace("\r\n", "\n").replace("\r", "\n").encode("utf-8")
    return hashlib.sha256(canonical).hexdigest()


def _sha256(path: Path) -> str:
    return _sha256_bytes(path.read_bytes())


def _safe_file(
    raw_path: Any,
    root: Path,
    errors: list[str],
    label: str,
) -> Path | None:
    if not isinstance(raw_path, str) or not raw_path:
        errors.append(f"{label}: path must be a non-empty string")
        return None
    candidate = (REPO / raw_path).resolve()
    try:
        candidate.relative_to(root.resolve())
    except ValueError:
        errors.append(f"{label}: path escapes {root.relative_to(REPO).as_posix()}: {raw_path}")
        return None
    if not candidate.is_file():
        errors.append(f"{label}: missing file {raw_path}")
        return None
    return candidate


def _validate_hash(
    path: Path | None,
    expected: Any,
    errors: list[str],
    label: str,
) -> None:
    if not isinstance(expected, str) or not _SHA256_RE.fullmatch(expected):
        errors.append(f"{label}: sha256 must be 64 lowercase hexadecimal characters")
        return
    if path is not None:
        actual = _sha256(path)
        if actual != expected:
            errors.append(f"{label}: stale sha256 (expected {expected}, actual {actual})")


def _git(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        ["git", "-C", str(REPO), *args],
        capture_output=True,
        text=True,
        check=False,
        env=_clean_git_environment(),
    )


def _git_blob(commit: str, path: str) -> str | None:
    result = _git("rev-parse", "--verify", f"{commit}:{path}")
    value = result.stdout.strip()
    return value if result.returncode == 0 and _GIT_OBJECT_RE.fullmatch(value) else None


def _git_file_sha256(commit: str, path: str) -> str | None:
    result = subprocess.run(
        ["git", "-C", str(REPO), "show", f"{commit}:{path}"],
        capture_output=True,
        check=False,
        env=_clean_git_environment(),
    )
    if result.returncode != 0:
        return None
    return _sha256_bytes(result.stdout)


def _plugin_version_at(commit: str) -> str | None:
    result = _git("show", f"{commit}:plugins/agent-bridge/plugin.json")
    if result.returncode != 0:
        return None
    try:
        data = json.loads(result.stdout)
    except json.JSONDecodeError:
        return None
    version = data.get("version")
    return version if isinstance(version, str) else None


def _integer_constant_at(commit: str, path: str, name: str) -> int | None:
    result = _git("show", f"{commit}:{path}")
    if result.returncode != 0:
        return None
    try:
        tree = ast.parse(result.stdout, filename=f"{commit}:{path}")
    except SyntaxError:
        return None
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and node.targets[0].id == name
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            return node.value.value
    return None


def _integer_constants(path: Path, errors: list[str], label: str) -> dict[str, int]:
    try:
        tree = ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    except (OSError, SyntaxError) as exc:
        errors.append(f"{label}: cannot parse constants: {exc}")
        return {}
    constants: dict[str, int] = {}
    for node in tree.body:
        if (
            isinstance(node, ast.Assign)
            and len(node.targets) == 1
            and isinstance(node.targets[0], ast.Name)
            and isinstance(node.value, ast.Constant)
            and isinstance(node.value.value, int)
        ):
            constants[node.targets[0].id] = node.value.value
    return constants


def _require_exact_keys(
    value: Any,
    required: set[str],
    errors: list[str],
    label: str,
    *,
    optional: set[str] | None = None,
) -> bool:
    if not isinstance(value, dict):
        errors.append(f"{label}: must be an object")
        return False
    optional = optional or set()
    missing = sorted(required - set(value))
    extra = sorted(set(value) - required - optional)
    if missing:
        errors.append(f"{label}: missing fields: {', '.join(missing)}")
    if extra:
        errors.append(f"{label}: unknown fields: {', '.join(extra)}")
    return not missing and not extra


def _validate_schema(errors: list[str]) -> None:
    schema = _load_json(SCHEMA_PATH, errors, "schema")
    if not isinstance(schema, dict):
        return
    if schema.get("$schema") != "https://json-schema.org/draft/2020-12/schema":
        errors.append("schema: $schema must select JSON Schema draft 2020-12")
    if schema.get("additionalProperties") is not False:
        errors.append("schema: top-level additionalProperties must be false")
    required = schema.get("required")
    if required != ["schema_version", "contracts", "deferred_contracts"]:
        errors.append("schema: top-level required fields are not the canonical ordered set")
    version = (
        schema.get("properties", {})
        .get("schema_version", {})
        .get("const")
    )
    if version != 1:
        errors.append("schema: schema_version const must be 1")
    properties = schema.get("properties")
    if not isinstance(properties, dict) or set(properties) != {
        "schema_version",
        "contracts",
        "deferred_contracts",
    }:
        errors.append("schema: top-level properties must match the registry shape")

    definitions = schema.get("$defs")
    if not isinstance(definitions, dict):
        errors.append("schema: $defs must be an object")
        return

    expected_definitions = {
        "sourcePath": (
            {"path", "sha256", "semantic", "non_semantic_reason"},
            {"path", "sha256", "semantic", "non_semantic_reason"},
        ),
        "fixture": (
            {"path", "sha256", "role", "generation"},
            {"path", "sha256", "role", "generation"},
        ),
        "runtimeProvenance": (
            {
                "commit",
                "plugin_version",
                "generation",
                "source_path",
                "source_git_blob",
                "capture_method",
            },
            {
                "commit",
                "plugin_version",
                "generation",
                "source_path",
                "source_git_blob",
                "capture_method",
            },
        ),
        "generationRange": (
            {
                "current",
                "minimum",
                "previous_generation",
                "previous_absent_reason",
            },
            {
                "current",
                "minimum",
                "previous_generation",
                "previous_absent_reason",
            },
        ),
        "contract": (_CONTRACT_KEYS, _CONTRACT_KEYS),
        "deferredContract": (
            {"id", "owner", "classification", "tracked_issue", "reason"},
            _DEFERRED_KEYS,
        ),
    }
    for name, (required_fields, property_fields) in expected_definitions.items():
        definition = definitions.get(name)
        label = f"schema.$defs.{name}"
        if not isinstance(definition, dict):
            errors.append(f"{label}: missing definition")
            continue
        if definition.get("additionalProperties") is not False:
            errors.append(f"{label}: additionalProperties must be false")
        required = definition.get("required")
        if not isinstance(required, list) or set(required) != required_fields:
            errors.append(f"{label}: required fields do not match checker contract")
        props = definition.get("properties")
        if not isinstance(props, dict) or set(props) != property_fields:
            errors.append(f"{label}: properties do not match checker contract")


def _validate_range(value: Any, errors: list[str], label: str) -> tuple[int | None, int | None]:
    required = {
        "current",
        "minimum",
        "previous_generation",
        "previous_absent_reason",
    }
    if not _require_exact_keys(value, required, errors, label):
        return None, None
    current = value["current"]
    minimum = value["minimum"]
    previous = value["previous_generation"]
    reason = value["previous_absent_reason"]
    if not isinstance(current, int) or isinstance(current, bool) or current < 1:
        errors.append(f"{label}.current: must be a positive integer")
    if not isinstance(minimum, int) or isinstance(minimum, bool) or minimum < 1:
        errors.append(f"{label}.minimum: must be a positive integer")
    if isinstance(current, int) and isinstance(minimum, int) and minimum > current:
        errors.append(f"{label}: minimum cannot exceed current")
    if previous is None:
        if not isinstance(reason, str) or not reason.strip():
            errors.append(
                f"{label}: previous_absent_reason is required when previous_generation is null"
            )
    else:
        if not isinstance(previous, int) or isinstance(previous, bool) or previous < 1:
            errors.append(f"{label}.previous_generation: must be a positive integer or null")
        if reason is not None:
            errors.append(
                f"{label}: previous_absent_reason must be null when previous_generation is present"
            )
    return (
        current if isinstance(current, int) and not isinstance(current, bool) else None,
        minimum if isinstance(minimum, int) and not isinstance(minimum, bool) else None,
    )


def _validate_contract(
    contract: Any,
    index: int,
    errors: list[str],
    ids: set[str],
    authorities: set[str],
    source_paths: set[str],
) -> tuple[int, set[tuple[str, str, int, str, str]]]:
    label = f"contracts[{index}]"
    if not _require_exact_keys(contract, _CONTRACT_KEYS, errors, label):
        return 0, set()

    contract_id = contract["id"]
    if not isinstance(contract_id, str) or not _ID_RE.fullmatch(contract_id):
        errors.append(
            f"{label}.id: must match ^[a-z0-9][a-z0-9.-]+$"
        )
        contract_id = label
    elif contract_id in ids:
        errors.append(f"{label}.id: duplicate contract id {contract_id}")
    else:
        ids.add(contract_id)
    authority = contract["authority"]
    if not isinstance(authority, str) or not authority:
        errors.append(f"{label}.authority: must be a non-empty string")
    elif authority in authorities:
        errors.append(f"{label}.authority: duplicate authority {authority}")
    else:
        authorities.add(authority)
    owner = contract["owner"]
    if not isinstance(owner, str) or not owner.strip():
        errors.append(f"{label}.owner: must be a non-empty string")
    if contract["kind"] not in {
        "wire-protocol",
        "semantic-contract",
        "external-reference",
        "representation-reference",
    }:
        errors.append(f"{label}.kind: unsupported kind {contract['kind']!r}")
    if not isinstance(contract["normative"], bool):
        errors.append(f"{label}.normative: must be boolean")

    current, minimum = _validate_range(
        contract["declared_range"], errors, f"{label}.declared_range"
    )
    evidence = contract["evidence_window"]
    evidence_generations: set[int] = set()
    evidence_runtimes: set[str] = set()
    if _require_exact_keys(
        evidence, {"generations", "runtimes"}, errors, f"{label}.evidence_window"
    ):
        generations = evidence["generations"]
        runtimes = evidence["runtimes"]
        if not isinstance(generations, list) or not generations:
            errors.append(f"{label}.evidence_window.generations: must be non-empty")
        else:
            for generation in generations:
                if (
                    not isinstance(generation, int)
                    or isinstance(generation, bool)
                    or generation < 1
                ):
                    errors.append(
                        f"{label}.evidence_window.generations: "
                        "values must be positive integers"
                    )
                else:
                    evidence_generations.add(generation)
            if len(evidence_generations) != len(generations):
                errors.append(
                    f"{label}.evidence_window.generations: values must be unique"
                )
            if current is not None and current not in evidence_generations:
                errors.append(f"{label}.evidence_window: current generation lacks evidence")
            previous = contract["declared_range"]["previous_generation"]
            if previous is not None and previous not in evidence_generations:
                errors.append(f"{label}.evidence_window: previous generation lacks evidence")
        if not isinstance(runtimes, list) or not runtimes:
            errors.append(f"{label}.evidence_window.runtimes: must be non-empty")
        else:
            for runtime in runtimes:
                if not isinstance(runtime, str) or not runtime:
                    errors.append(
                        f"{label}.evidence_window.runtimes: "
                        "values must be non-empty strings"
                    )
                else:
                    evidence_runtimes.add(runtime)
            if len(evidence_runtimes) != len(runtimes):
                errors.append(f"{label}.evidence_window.runtimes: values must be unique")

    capabilities = contract["capability_versions"]
    if not isinstance(capabilities, dict):
        errors.append(f"{label}.capability_versions: must be an object")
    else:
        for name, generation in sorted(capabilities.items()):
            if not isinstance(name, str) or not name:
                errors.append(f"{label}.capability_versions: names must be non-empty strings")
            if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
                errors.append(
                    f"{label}.capability_versions.{name}: must be a positive integer"
                )
    if not isinstance(contract["durable_records"], list):
        errors.append(f"{label}.durable_records: must be an array")

    local_sources: set[str] = set()
    source_records: dict[str, tuple[str, bool]] = {}
    sources = contract["source_paths"]
    if not isinstance(sources, list) or not sources:
        errors.append(f"{label}.source_paths: must be a non-empty array")
    else:
        for source_index, source in enumerate(sources):
            source_label = f"{label}.source_paths[{source_index}]"
            if not _require_exact_keys(
                source,
                {"path", "sha256", "semantic", "non_semantic_reason"},
                errors,
                source_label,
            ):
                continue
            raw_path = source["path"]
            if isinstance(raw_path, str):
                if raw_path in local_sources:
                    errors.append(f"{source_label}: duplicate source path {raw_path}")
                local_sources.add(raw_path)
                source_paths.add(raw_path)
            path = _safe_file(raw_path, REPO, errors, source_label)
            _validate_hash(path, source["sha256"], errors, source_label)
            semantic = source["semantic"]
            reason = source["non_semantic_reason"]
            if not isinstance(semantic, bool):
                errors.append(f"{source_label}.semantic: must be boolean")
            elif not semantic and (not isinstance(reason, str) or not reason.strip()):
                errors.append(
                    f"{source_label}: non_semantic_reason is required when semantic is false"
                )
            elif semantic and reason is not None:
                errors.append(
                    f"{source_label}: non_semantic_reason must be null when semantic is true"
                )
            if (
                isinstance(raw_path, str)
                and isinstance(source["sha256"], str)
                and isinstance(semantic, bool)
            ):
                source_records[raw_path] = (source["sha256"], semantic)

    provenance_keys: set[tuple[str, str, int, str, str]] = set()
    runtime_generation_keys: set[tuple[str, int]] = set()
    provenances = contract["provenance"]
    if not isinstance(provenances, list) or not provenances:
        errors.append(f"{label}.provenance: must be a non-empty array")
    else:
        for prov_index, provenance in enumerate(provenances):
            prov_label = f"{label}.provenance[{prov_index}]"
            required = {
                "commit",
                "plugin_version",
                "generation",
                "source_path",
                "source_git_blob",
                "capture_method",
            }
            if not _require_exact_keys(provenance, required, errors, prov_label):
                continue
            commit = provenance["commit"]
            version = provenance["plugin_version"]
            generation = provenance["generation"]
            source_path = provenance["source_path"]
            blob = provenance["source_git_blob"]
            if not isinstance(commit, str) or not _GIT_OBJECT_RE.fullmatch(commit):
                errors.append(f"{prov_label}.commit: must be a full lowercase Git commit")
                continue
            if not isinstance(version, str) or not version:
                errors.append(f"{prov_label}.plugin_version: must be non-empty")
            if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
                errors.append(f"{prov_label}.generation: must be a positive integer")
                continue
            if not isinstance(source_path, str) or not source_path:
                errors.append(f"{prov_label}.source_path: must be non-empty")
                continue
            if source_path not in local_sources:
                errors.append(
                    f"{prov_label}.source_path: not registered in source_paths: "
                    f"{source_path}"
                )
            if not isinstance(blob, str) or not _GIT_OBJECT_RE.fullmatch(blob):
                errors.append(f"{prov_label}.source_git_blob: must be a full lowercase Git blob")
            else:
                actual_blob = _git_blob(commit, source_path)
                if actual_blob is None:
                    errors.append(
                        f"{prov_label}: cannot resolve {commit}:{source_path}"
                    )
                elif actual_blob != blob:
                    errors.append(
                        f"{prov_label}: source_git_blob is {blob}, actual {actual_blob}"
                    )
            actual_version = _plugin_version_at(commit)
            if actual_version is None:
                errors.append(f"{prov_label}: cannot resolve agent-bridge version at {commit}")
            elif actual_version != version:
                errors.append(
                    f"{prov_label}: plugin_version is {version}, actual {actual_version}"
                )
            method = provenance["capture_method"]
            if not isinstance(method, str) or not method.strip():
                errors.append(f"{prov_label}.capture_method: must be non-empty")
            if isinstance(version, str):
                provenance_keys.add(
                    (commit, version, generation, source_path, blob)
                )
                runtime_generation_keys.add((version, generation))
            if contract_id == "agent-bridge.http-wire" and source_path.endswith(
                "/agent_bridge/protocol.py"
            ):
                historical_generation = _integer_constant_at(
                    commit, source_path, "HTTP_PROTOCOL_VERSION"
                )
                if historical_generation != generation:
                    errors.append(
                        f"{prov_label}: generation {generation} does not match "
                        f"historical HTTP_PROTOCOL_VERSION={historical_generation!r}"
                    )
            if (
                contract_id == "agent-bridge.session-host-wire"
                and source_path.endswith("/session_host/protocol.py")
            ):
                historical_generation = _integer_constant_at(
                    commit, source_path, "PROTOCOL_VERSION"
                )
                if historical_generation != generation:
                    errors.append(
                        f"{prov_label}: generation {generation} does not match "
                        f"historical PROTOCOL_VERSION={historical_generation!r}"
                    )

    fixture_count = 0
    local_fixtures: set[str] = set()
    fixture_generations: set[int] = set()
    fixture_runtimes: set[str] = set()
    current_fixture_sources: set[str] = set()
    fixtures = contract["fixtures"]
    if not isinstance(fixtures, list) or not fixtures:
        errors.append(f"{label}.fixtures: must be a non-empty array")
    else:
        for fixture_index, fixture in enumerate(fixtures):
            fixture_label = f"{label}.fixtures[{fixture_index}]"
            if not _require_exact_keys(
                fixture, {"path", "sha256", "role", "generation"}, errors, fixture_label
            ):
                continue
            raw_path = fixture["path"]
            if isinstance(raw_path, str):
                if raw_path in local_fixtures:
                    errors.append(f"{fixture_label}: duplicate fixture path {raw_path}")
                local_fixtures.add(raw_path)
            path = _safe_file(raw_path, CONTRACT_DIR, errors, fixture_label)
            _validate_hash(path, fixture["sha256"], errors, fixture_label)
            if not isinstance(fixture["role"], str) or not fixture["role"].strip():
                errors.append(f"{fixture_label}.role: must be non-empty")
            generation = fixture["generation"]
            if not isinstance(generation, int) or isinstance(generation, bool) or generation < 1:
                errors.append(f"{fixture_label}.generation: must be a positive integer")
            fixture_data = _load_json(path, errors, fixture_label) if path is not None else None
            if isinstance(fixture_data, dict):
                captured = fixture_data.get("captured_from")
                if not isinstance(captured, dict):
                    errors.append(f"{fixture_label}: fixture lacks captured_from provenance")
                else:
                    captured_generation = captured.get("protocol_generation")
                    captured_runtime = captured.get("plugin_version")
                    captured_source = captured.get("source_path")
                    captured_blob = captured.get("source_git_blob")
                    captured_sha256 = captured.get("source_sha256")
                    key = (
                        captured.get("commit"),
                        captured_runtime,
                        captured_generation,
                        captured_source,
                        captured_blob,
                    )
                    if key not in provenance_keys:
                        errors.append(
                            f"{fixture_label}: captured_from does not match contract provenance"
                        )
                    if (
                        not isinstance(captured_sha256, str)
                        or not _SHA256_RE.fullmatch(captured_sha256)
                    ):
                        errors.append(
                            f"{fixture_label}: captured_from.source_sha256 must "
                            "be 64 lowercase hexadecimal characters"
                        )
                    elif isinstance(captured.get("commit"), str) and isinstance(
                        captured_source, str
                    ):
                        historical_sha256 = _git_file_sha256(
                            captured["commit"], captured_source
                        )
                        if historical_sha256 != captured_sha256:
                            errors.append(
                                f"{fixture_label}: source_sha256 is "
                                f"{captured_sha256}, historical source is "
                                f"{historical_sha256!r}"
                            )
                    if not isinstance(captured_source, str) or captured_source not in source_records:
                        errors.append(
                            f"{fixture_label}: captured source is not registered "
                            f"in source_paths: {captured_source!r}"
                        )
                    elif (
                        captured_generation == current
                        and isinstance(captured_sha256, str)
                        and captured_sha256 == source_records[captured_source][0]
                    ):
                        current_fixture_sources.add(captured_source)
                    if captured_generation != generation:
                        errors.append(
                            f"{fixture_label}: fixture generation {generation} does not "
                            f"match captured_from generation {captured_generation!r}"
                        )
                    if (
                        isinstance(captured_generation, int)
                        and captured_generation not in evidence_generations
                    ):
                        errors.append(
                            f"{fixture_label}: captured generation "
                            f"{captured_generation} is outside evidence_window"
                        )
                    else:
                        if isinstance(captured_generation, int):
                            fixture_generations.add(captured_generation)
                    if (
                        not isinstance(captured_runtime, str)
                        or captured_runtime not in evidence_runtimes
                    ):
                        errors.append(
                            f"{fixture_label}: captured runtime "
                            f"{captured_runtime!r} is outside evidence_window"
                        )
                    else:
                        fixture_runtimes.add(captured_runtime)
                    if (
                        isinstance(captured_runtime, str)
                        and isinstance(captured_generation, int)
                        and (captured_runtime, captured_generation)
                        not in runtime_generation_keys
                    ):
                        errors.append(
                            f"{fixture_label}: runtime/generation pair lacks "
                            "source provenance"
                        )
            fixture_count += 1

    missing_generations = sorted(evidence_generations - fixture_generations)
    if missing_generations:
        errors.append(
            f"{label}.evidence_window: generations lack fixtures: "
            + ", ".join(str(value) for value in missing_generations)
        )
    missing_runtimes = sorted(evidence_runtimes - fixture_runtimes)
    if missing_runtimes:
        errors.append(
            f"{label}.evidence_window: runtimes lack fixtures: "
            + ", ".join(missing_runtimes)
        )
    missing_current_sources = sorted(
        path
        for path, (_sha, semantic) in source_records.items()
        if semantic and path not in current_fixture_sources
    )
    if missing_current_sources:
        errors.append(
            f"{label}: semantic current sources lack matching current fixtures: "
            + ", ".join(missing_current_sources)
        )

    for field in (
        "support_window",
        "bridge_contract_rollback_window",
        "removal_gate",
    ):
        if not isinstance(contract[field], str) or not contract[field].strip():
            errors.append(f"{label}.{field}: must be a non-empty string")
    scenarios = contract["mixed_version_scenarios"]
    if (
        not isinstance(scenarios, list)
        or not scenarios
        or any(not isinstance(item, str) or not item for item in scenarios)
    ):
        errors.append(f"{label}.mixed_version_scenarios: must be non-empty strings")

    if contract_id == "agent-bridge.http-wire":
        constants = _integer_constants(
            REPO / "plugins/agent-bridge/src/agent_bridge/protocol.py",
            errors,
            f"{label}.production_constants",
        )
        expected = {
            "HTTP_PROTOCOL_VERSION": current,
            "HTTP_PROTOCOL_MIN_SUPPORTED": minimum,
        }
        for name, value in expected.items():
            if value is not None and constants.get(name) != value:
                errors.append(
                    f"{label}: {name} registry value {value} does not match "
                    f"production value {constants.get(name)!r}"
                )
        if isinstance(capabilities, dict):
            for registry_name, constant_name in _HTTP_CAPABILITY_CONSTANTS.items():
                if capabilities.get(registry_name) != constants.get(constant_name):
                    errors.append(
                        f"{label}: capability {registry_name} does not match "
                        f"{constant_name}={constants.get(constant_name)!r}"
                    )
    elif contract_id == "agent-bridge.session-host-wire":
        constants = _integer_constants(
            REPO / "plugins/agent-bridge/src/agent_bridge/session_host/protocol.py",
            errors,
            f"{label}.production_constants",
        )
        if current is not None and constants.get("PROTOCOL_VERSION") != current:
            errors.append(
                f"{label}: PROTOCOL_VERSION registry value {current} does not match "
                f"production value {constants.get('PROTOCOL_VERSION')!r}"
            )

    return fixture_count, provenance_keys


def _validate_deferred(
    item: Any,
    index: int,
    errors: list[str],
    ids: set[str],
) -> None:
    label = f"deferred_contracts[{index}]"
    required = {"id", "owner", "classification", "tracked_issue", "reason"}
    optional = {"reference_path", "reference_sha256"}
    if not _require_exact_keys(item, required, errors, label, optional=optional):
        return
    item_id = item["id"]
    if not isinstance(item_id, str) or not _ID_RE.fullmatch(item_id):
        errors.append(
            f"{label}.id: must match ^[a-z0-9][a-z0-9.-]+$"
        )
    elif item_id in ids:
        errors.append(f"{label}.id: duplicate registry id {item_id}")
    else:
        ids.add(item_id)
    if item["classification"] not in {"bridge-owned", "externally-owned", "deferred"}:
        errors.append(f"{label}.classification: unsupported classification")
    for field in ("owner", "tracked_issue", "reason"):
        if not isinstance(item[field], str) or not item[field].strip():
            errors.append(f"{label}.{field}: must be a non-empty string")
    has_path = "reference_path" in item
    has_hash = "reference_sha256" in item
    if item["classification"] == "externally-owned" and not (has_path and has_hash):
        errors.append(
            f"{label}: externally-owned references require "
            "reference_path and reference_sha256"
        )
    if has_path != has_hash:
        errors.append(f"{label}: reference_path and reference_sha256 must appear together")
    elif has_path:
        path = _safe_file(item["reference_path"], REPO, errors, label)
        _validate_hash(path, item["reference_sha256"], errors, label)


def _changed_files(base_ref: str, errors: list[str]) -> set[str]:
    base = _git("rev-parse", "--verify", "--quiet", base_ref)
    if base.returncode != 0 or not base.stdout.strip():
        errors.append(f"diff: cannot resolve base {base_ref!r}")
        return set()
    head = _git("rev-parse", "--verify", "--quiet", "HEAD")
    if head.returncode != 0 or not head.stdout.strip():
        errors.append("diff: cannot resolve HEAD")
        return set()
    merge_base = _git("merge-base", base.stdout.strip(), head.stdout.strip())
    start = merge_base.stdout.strip() if merge_base.returncode == 0 else base.stdout.strip()
    diff = _git("diff", "--name-only", f"{start}..{head.stdout.strip()}")
    if diff.returncode != 0:
        errors.append(f"diff: git diff failed: {diff.stderr.strip()}")
        return set()
    return {line.strip() for line in diff.stdout.splitlines() if line.strip()}


def check(base_ref: str | None = None) -> tuple[int, list[str], int, int]:
    errors: list[str] = []
    _validate_schema(errors)
    registry = _load_json(REGISTRY_PATH, errors, "registry")
    if not isinstance(registry, dict):
        return 1, sorted(set(errors)), 0, 0
    if not _require_exact_keys(
        registry,
        {"schema_version", "contracts", "deferred_contracts"},
        errors,
        "registry",
    ):
        return 1, sorted(set(errors)), 0, 0
    if registry["schema_version"] != 1:
        errors.append("registry.schema_version: only version 1 is supported")

    ids: set[str] = set()
    authorities: set[str] = set()
    source_paths: set[str] = set()
    contracts = registry["contracts"]
    fixture_count = 0
    if not isinstance(contracts, list) or not contracts:
        errors.append("registry.contracts: must be a non-empty array")
        contracts = []
    for index, contract in enumerate(contracts):
        count, _ = _validate_contract(
            contract, index, errors, ids, authorities, source_paths
        )
        fixture_count += count
    missing_protocol_contracts = sorted(_REQUIRED_PROTOCOL_CONTRACTS - ids)
    if missing_protocol_contracts:
        errors.append(
            "registry.contracts: missing required protocol contracts: "
            + ", ".join(missing_protocol_contracts)
        )

    deferred = registry["deferred_contracts"]
    if not isinstance(deferred, list):
        errors.append("registry.deferred_contracts: must be an array")
        deferred = []
    for index, item in enumerate(deferred):
        _validate_deferred(item, index, errors, ids)

    if base_ref is not None:
        changed = _changed_files(base_ref, errors)
        changed_registered = sorted(changed & source_paths)
        if changed_registered and (
            "plugins/agent-bridge/contract/registry.json" not in changed
        ):
            errors.append(
                "diff: registered contract source changed without updating "
                "plugins/agent-bridge/contract/registry.json: "
                + ", ".join(changed_registered)
            )

    return (1 if errors else 0), sorted(set(errors)), len(contracts), fixture_count


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description=__doc__,
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    parser.add_argument(
        "--base",
        help="optional base ref for diff-scoped registered-source coverage",
    )
    args = parser.parse_args(argv)

    code, errors, contract_count, fixture_count = check(args.base)
    if errors:
        print("check-agent-bridge-contracts: FAILED", file=sys.stderr)
        for error in errors:
            print(f"  - {error}", file=sys.stderr)
        return 1
    print(
        "check-agent-bridge-contracts: OK "
        f"({contract_count} contracts, {fixture_count} fixtures)"
    )
    return code


if __name__ == "__main__":
    raise SystemExit(main())
