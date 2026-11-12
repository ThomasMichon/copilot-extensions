"""Validate the shipped topology contract; no native tools or external schemas."""

from __future__ import annotations

import json
import hashlib
from datetime import datetime
from pathlib import Path
from typing import Any, cast

from topology_dispatch import linked

ASSETS = Path(__file__).resolve().parents[1] / "assets"
MAX_BYTES = 2 * 1024 * 1024
TERMINAL_EFFECTS = {"accepted", "confirmed", "failed"}
SCHEMA_KEYWORDS = {
    "$schema", "$defs", "$ref", "title", "description", "type", "const", "enum",
    "properties", "required", "additionalProperties", "items", "maxItems",
    "uniqueItems", "minLength", "maxLength", "minimum", "maximum",
}


class ContractError(ValueError):
    pass


def _pairs(pairs: list[tuple[str, Any]]) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for key, value in pairs:
        if key in result:
            raise ContractError(f"duplicate JSON key: {key}")
        result[key] = value
    return result


def _constant(value: str) -> None:
    raise ContractError(f"non-finite JSON number: {value}")


def read_json(path: Path) -> Any:
    with path.open("rb") as stream:
        data = stream.read(MAX_BYTES + 1)
    if len(data) > MAX_BYTES:
        raise ContractError("document exceeds 2 MiB")
    return json.loads(data.decode("utf-8-sig"), object_pairs_hook=_pairs, parse_constant=_constant)


def timestamp(value: str) -> datetime:
    try:
        result = datetime.fromisoformat(value.replace("Z", "+00:00"))
        if result.tzinfo is None:
            raise ValueError("timezone required")
        return result
    except ValueError as exc:
        raise ContractError("observation time must be an ISO timestamp with timezone") from exc


def owner_key(owner: dict[str, str]) -> tuple[str, str, str]:
    return owner["machine"], owner["project"], owner["worktree"]


def content_revision(value: Any) -> str:
    encoded = json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False)
    return "sha256:" + hashlib.sha256(encoded.encode("utf-8")).hexdigest()


def validate_materialization(document: Any) -> dict[str, Any]:
    """Inspect an exported charter without asserting native acceptance or authority."""
    schema = read_json(ASSETS / "topology.schema.json")
    _shape(document, schema["$defs"]["materialization"], schema, "$")
    effective = document["effective"]
    source = document["source"]
    if effective["charter"]["scope"] != effective["role"]["scope"]:
        raise ContractError("materialized charter scope differs from its role")
    if source["machine"] != effective["machine"]["id"] or source["role"] != effective["role"]["id"]:
        raise ContractError("materialized source differs from its selected role or machine")
    if document["materializationRevision"] != content_revision(effective):
        raise ContractError("materialization content revision differs")
    if document["sourceRevision"] != content_revision({"source": source, "effective": effective}):
        raise ContractError("materialization source revision differs")
    return cast(dict[str, Any], document)


def validate_admission(document: Any, topology: dict[str, Any]) -> dict[str, Any]:
    schema = read_json(ASSETS / "admission.schema.json")
    _shape(document, schema, schema, "$")
    timestamp(document["observedAt"])
    machines = {item["id"] for item in topology["machines"]}
    roles = {item["id"] for item in topology["roles"]}
    if set(document["reviewRoles"]) - roles:
        raise ContractError("admission review role is not declared")
    seen_machines: set[str] = set()
    for machine in document["machines"]:
        name = machine["machine"]
        if name not in machines or name in seen_machines:
            raise ContractError("admission machine is unknown or duplicated")
        seen_machines.add(name)
    tasks: set[str] = set()
    requests: set[str] = set()
    for task in document["tasks"]:
        timestamp(task["observedAt"])
        if task["role"] not in roles:
            raise ContractError("admission task role is not declared")
        if task["id"] in tasks or task["requestId"] in requests:
            raise ContractError("admission task or request is duplicated")
        tasks.add(task["id"])
        requests.add(task["requestId"])
        if task["owner"] is not None and task["owner"]["machine"] not in machines:
            raise ContractError("admission retained owner machine is not declared")
    return cast(dict[str, Any], document)


def _shape(value: Any, rule: dict[str, Any], schema: dict[str, Any], path: str) -> None:
    # Only the vocabulary used by our bundled schema, never an input-supplied schema.
    if rule.keys() - SCHEMA_KEYWORDS:
        raise ContractError(f"{path}: unsupported bundled schema keyword")
    if "$ref" in rule:
        reference = rule["$ref"]
        if (
            not reference.startswith("#/$defs/")
            or reference.removeprefix("#/$defs/") not in schema["$defs"]
            or rule.keys() - {"$ref", "title", "description"}
        ):
            raise ContractError(f"{path}: unsupported bundled schema reference")
        _shape(value, schema["$defs"][reference.removeprefix("#/$defs/")], schema, path)
        return
    types = {dict: "object", list: "array", str: "string", int: "integer", bool: "boolean", type(None): "null"}
    allowed = rule.get("type", list(types.values()))
    if isinstance(allowed, str):
        allowed = [allowed]
    if types.get(type(value)) not in allowed:
        raise ContractError(f"{path}: wrong type")
    if "const" in rule and value != rule["const"]:
        raise ContractError(f"{path}: unsupported value/version")
    if "enum" in rule and value not in rule["enum"]:
        raise ContractError(f"{path}: unsupported value")
    if isinstance(value, dict):
        properties = rule.get("properties", {})
        missing = set(rule.get("required", [])) - value.keys()
        extra = value.keys() - properties.keys() if rule.get("additionalProperties") is False else set()
        if missing or extra:
            raise ContractError(f"{path}: missing {sorted(missing)}, unknown {sorted(extra)}")
        for key, child in value.items():
            if key in properties:
                _shape(child, properties[key], schema, f"{path}.{key}")
    elif isinstance(value, list):
        if len(value) > rule.get("maxItems", MAX_BYTES):
            raise ContractError(f"{path}: too many items")
        if rule.get("uniqueItems") and len({json.dumps(item, sort_keys=True) for item in value}) != len(value):
            raise ContractError(f"{path}: duplicate items")
        for index, child in enumerate(value):
            _shape(child, rule["items"], schema, f"{path}[{index}]")
    elif isinstance(value, str):
        if not rule.get("minLength", 0) <= len(value) <= rule.get("maxLength", MAX_BYTES):
            raise ContractError(f"{path}: invalid text length")
    elif type(value) is int:
        if value < rule.get("minimum", value) or value > rule.get("maximum", value):
            raise ContractError(f"{path}: outside allowed range")


def _unique(values: list[Any], label: str) -> None:
    if len(values) != len(set(values)):
        raise ContractError(f"duplicate {label}")


def validate(document: Any) -> dict[str, Any]:
    schema = read_json(ASSETS / "topology.schema.json")
    _shape(document, schema, schema, "$")
    for key in ("machines", "roles", "assignments"):
        _unique([item["id"] for item in document[key]], key)
    machines = {item["id"]: item for item in document["machines"]}
    roles = {item["id"]: item for item in document["roles"]}
    assignments = {item["id"]: item for item in document["assignments"]}
    for field in ("requestId", "creationKey"):
        _unique([item[field] for item in assignments.values()], field)
    if document["dispatcher"] is not None and document["dispatcher"]["owner"]["machine"] not in machines:
        raise ContractError("dispatcher references an unknown machine")
    _unique([item["nativeTask"] for item in assignments.values()
             if item["nativeTask"] is not None], "native task reference")
    for machine in machines.values():
        if set(machine["allowedRoles"]) - roles.keys():
            raise ContractError("machine references an unknown role")
    effects = []
    followups = []
    for assignment in assignments.values():
        if assignment["machine"] not in machines or assignment["role"] not in roles:
            raise ContractError("assignment references an unknown machine or role")
        if (assignment["state"] not in {"completed", "released"}
                and assignment["charter"]["scope"] != roles[assignment["role"]]["scope"]):
            raise ContractError("assignment charter scope differs from its role")
        owner = assignment["nativeWorktree"]
        if owner and owner["machine"] != assignment["machine"]:
            raise ContractError("native worktree differs from selected machine")
        if assignment["state"] != "proposed" and owner is None and not linked(assignment, document["observations"]):
            raise ContractError("non-proposed assignment requires a native worktree")
        by_effect = {effect["id"]: effect for effect in assignment["effects"]}
        for effect in assignment["effects"]:
            effects.append(effect["id"])
            lifecycle = effect["kind"] in {"allocate", "retire"}
            if lifecycle and effect["state"] in {"admitted", "consumed", "accepted"}:
                raise ContractError("lifecycle effects require confirmed native outcomes")
            if not lifecycle and effect["state"] == "confirmed":
                raise ContractError("message effects distinguish admission, consumption and acceptance")
            if not lifecycle and effect["nativeRequest"] is None:
                raise ContractError("message effects require their original native request envelope")
            if effect["charterRevision"] > assignment["charter"]["revision"]:
                raise ContractError("effect references a future charter")
            if effect["kind"] != "allocate" and (owner is None or effect["target"] != owner):
                raise ContractError("effect must target its retained native worktree")
            if effect["target"] is not None and effect["target"] != owner:
                raise ContractError("effect target conflicts with native worktree")
            if effect["kind"] == "allocate" and effect["state"] == "confirmed" and owner is None:
                raise ContractError("confirmed allocation requires a native worktree")
            if effect["state"] not in {"prepared", "unknown", "failed"} and not effect["nativeRef"]:
                raise ContractError("observed effect requires its native receipt reference")
        if assignment["state"] == "released" and (
            any(effect["state"] not in TERMINAL_EFFECTS for effect in assignment["effects"])
            or not any(effect["kind"] == "retire" and effect["state"] == "confirmed"
                       for effect in assignment["effects"])
        ):
            raise ContractError("released assignment requires settled effects and native retirement")
        _unique([item["effectId"] for item in assignment["followups"]
                 if item["effectId"] is not None], "follow-up relay")
        for followup in assignment["followups"]:
            followups.append(followup["id"])
            if followup["charterRevision"] > assignment["charter"]["revision"]:
                raise ContractError("follow-up references a future charter")
            effect = by_effect.get(followup["effectId"])
            if followup["effectId"] is not None and (
                not effect or effect["kind"] != "relay"
                or effect["charterRevision"] != followup["charterRevision"]
            ):
                raise ContractError("follow-up must reference its own matching relay")
    _unique(effects, "effect identity")
    _unique(followups, "follow-up identity")
    observations = document["observations"]
    _unique([item["machine"] for item in observations["machines"]], "machine observation")
    _unique([owner_key(item["owner"]) for item in observations["owners"]], "owner observation")
    _unique([item["task"]["id"] for item in observations.get("dispatch", [])], "native task observation")
    for observation in [observations["dispatcher"], observations["discovery"],
                        *observations["machines"], *observations["owners"],
                        *observations.get("dispatch", [])]:
        if observation is not None:
            timestamp(observation["observedAt"])
    for observation in observations["machines"]:
        if observation["machine"] not in machines:
            raise ContractError("observation references an unknown machine")
    for observation in observations["owners"]:
        if observation["owner"]["machine"] not in machines:
            raise ContractError("owner observation references an unknown machine")
        progress = observation.get("progress")
        if progress is not None:
            observed = timestamp(progress["observedAt"])
            advanced = progress["lastAdvancedAt"]
            if advanced is not None and timestamp(advanced) > observed:
                raise ContractError("progress cannot advance after it was observed")
        assignment_id = observation["assignmentId"]
        if assignment_id is not None:
            assignment = assignments.get(assignment_id)
            if assignment is None or (
                assignment["nativeWorktree"] != observation["owner"]
                and not (linked(assignment, observations) and assignment["nativeWorktree"] is None)
            ):
                raise ContractError("owner observation conflicts with its assignment")
    return cast(dict[str, Any], document)
