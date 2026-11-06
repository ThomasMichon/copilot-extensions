"""Bounded portable preference data; no configuration or binding effects."""

from __future__ import annotations

import hashlib
import json
import os
import re
import stat
from copy import deepcopy
from pathlib import Path

MAX_BYTES = 256 * 1024
MAX_RELATIONSHIPS = 128
MAX_MACHINES = 64
MAX_DEPTH = 16
SCHEMA = "agent-machines.preferences"
LOCAL_SCHEMA = "agent-machines.preferences.local"
_IDENTIFIER = re.compile(r"[A-Za-z0-9][A-Za-z0-9._:-]{0,127}", re.ASCII)
_DOMAINS = {"copilot", "telemetry", "repositories", "maintenance"}


class PreferenceError(ValueError):
    """A stable reason code, never arbitrary synchronized content."""


def _object(value, required=(), optional=()):
    if not isinstance(value, dict) or set(value) - set(required) - set(optional):
        raise PreferenceError("invalid-fields")
    if set(required) - set(value):
        raise PreferenceError("missing-fields")
    return value


def _identifier(value):
    if not isinstance(value, str) or not _IDENTIFIER.fullmatch(value):
        raise PreferenceError("invalid-identifier")
    return value


def _choices(value, choices):
    if not isinstance(value, str) or value not in choices:
        raise PreferenceError("invalid-choice")


def _pairs(pairs):
    result = {}
    for key, value in pairs:
        if key in result:
            raise PreferenceError("duplicate-key")
        result[key] = value
    return result


def _constant(_value):
    raise PreferenceError("invalid-number")


def decode(raw: bytes) -> dict:
    if len(raw) > MAX_BYTES:
        raise PreferenceError("document-too-large")
    try:
        value = json.loads(raw.decode("utf-8"), object_pairs_hook=_pairs, parse_constant=_constant)
    except PreferenceError:
        raise
    except (ValueError, RecursionError) as error:
        raise PreferenceError("invalid-json") from error
    pending = [(value, 0)]
    while pending:
        item, depth = pending.pop()
        if depth > MAX_DEPTH:
            raise PreferenceError("document-too-deep")
        if isinstance(item, dict):
            pending.extend((child, depth + 1) for child in item.values())
        elif isinstance(item, list):
            pending.extend((child, depth + 1) for child in item)
    return value


def read(path: Path, *, optional=False, local=False) -> dict | None:
    descriptor = None
    try:
        before = path.lstat()
        if (
            not stat.S_ISREG(before.st_mode) or path.is_symlink()
            or getattr(before, "st_file_attributes", 0) & 0x400
        ):
            raise PreferenceError("input-not-regular")
        descriptor = os.open(
            path, os.O_RDONLY | getattr(os, "O_NOFOLLOW", 0) | getattr(os, "O_NONBLOCK", 0),
        )
        opened = os.fstat(descriptor)
        if (
            not stat.S_ISREG(opened.st_mode)
            or (before.st_dev, before.st_ino) != (opened.st_dev, opened.st_ino)
        ):
            raise PreferenceError("input-changed")
        if opened.st_size > MAX_BYTES:
            raise PreferenceError("document-too-large")
        with os.fdopen(descriptor, "rb") as stream:
            descriptor = None
            raw = stream.read(MAX_BYTES + 1)
            after = os.fstat(stream.fileno())
        if (opened.st_size, opened.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
            raise PreferenceError("input-changed")
        return validate_local(decode(raw)) if local else validate(decode(raw))
    except FileNotFoundError as error:
        if optional:
            return None
        raise PreferenceError("input-unavailable") from error
    except OSError as error:
        raise PreferenceError("input-unavailable") from error
    finally:
        if descriptor is not None:
            os.close(descriptor)


def _domains(value, *, local=False) -> int:
    _object(value, optional=_DOMAINS)
    relationships = 0
    if "copilot" in value:
        copilot = _object(value["copilot"], optional=("model", "effortLevel"))
        if "model" in copilot:
            _identifier(copilot["model"])
        if "effortLevel" in copilot:
            _choices(copilot["effortLevel"], {"minimal", "low", "medium", "high", "xhigh", "max"})
    if "telemetry" in value:
        telemetry = _object(value["telemetry"], optional=("capture", "reporting"))
        if "capture" in telemetry:
            _choices(telemetry["capture"], {"disabled", "retained-local"})
        if "reporting" in telemetry:
            _choices(telemetry["reporting"], {"disabled", "local"})
    if "maintenance" in value:
        maintenance = _object(value["maintenance"], optional=("updates",))
        if "updates" in maintenance:
            _choices(maintenance["updates"], {"manual", "automatic"})
    if "repositories" in value:
        repositories = _object(value["repositories"], optional=("knowledge", "venues"))
        for field, key in (("knowledge", "harness_repository_id"), ("venues", "repository_id")):
            records = repositories.get(field, [])
            if not isinstance(records, list) or len(records) > MAX_RELATIONSHIPS:
                raise PreferenceError("too-many-relationships")
            seen = set()
            for record in records:
                other = "knowledge_repository_id" if field == "knowledge" else "venue"
                _object(record, required=(key, other))
                identity = _identifier(record[key])
                if identity in seen:
                    raise PreferenceError("duplicate-relationship")
                seen.add(identity)
                target = record[other]
                if local and target is None:
                    continue
                if field == "knowledge":
                    _identifier(target)
                else:
                    _choices(target, {"local", "codespace", "container", "ssh"})
            relationships += len(records)
    return relationships


def validate(value) -> dict:
    _object(value, required=("schema", "version", "publisher", "revision", "parent", "domains"),
            optional=("machines",))
    if value["schema"] != SCHEMA or type(value["version"]) is not int or value["version"] != 1:
        raise PreferenceError("unsupported-schema")
    _identifier(value["publisher"])
    revision, parent = value["revision"], value["parent"]
    if type(revision) is not int or not 1 <= revision <= 2**53 - 1:
        raise PreferenceError("invalid-revision")
    if parent is not None and (type(parent) is not int or not 1 <= parent < revision):
        raise PreferenceError("invalid-parent")
    count = _domains(value["domains"])
    machines = value.get("machines", {})
    if not isinstance(machines, dict) or len(machines) > MAX_MACHINES:
        raise PreferenceError("too-many-machines")
    seen = set()
    for machine, domains in machines.items():
        normalized = _identifier(machine).casefold()
        if normalized in seen:
            raise PreferenceError("duplicate-machine")
        seen.add(normalized)
        count += _domains(domains)
    if count > MAX_RELATIONSHIPS:
        raise PreferenceError("too-many-relationships")
    return deepcopy(value)


def validate_local(value) -> dict:
    _object(value, required=("schema", "version", "machine", "domains"))
    if value["schema"] != LOCAL_SCHEMA or type(value["version"]) is not int or value["version"] != 1:
        raise PreferenceError("unsupported-schema")
    _identifier(value["machine"])
    if _domains(value["domains"], local=True) > MAX_RELATIONSHIPS:
        raise PreferenceError("too-many-relationships")
    return deepcopy(value)


def digest(value: dict) -> str:
    raw = json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _overlay(target, source, origin, provenance):
    for domain, values in source.items():
        selected = target.setdefault(domain, {})
        for field, value in values.items():
            if domain == "repositories":
                key = "harness_repository_id" if field == "knowledge" else "repository_id"
                records = {record[key]: record for record in selected.get(field, [])}
                for record in value:
                    records[record[key]] = deepcopy(record)
                    provenance[f"{domain}.{field}:{record[key]}"] = origin
                selected[field] = list(records.values())
            else:
                selected[field] = deepcopy(value)
                provenance[f"{domain}.{field}"] = origin


def preview(candidate, accepted, *, publisher, machine, harness, local=None, conflict=False) -> dict:
    """Project supplied scope only; native identity and adoption remain separate."""
    _identifier(publisher)
    _identifier(machine)
    _identifier(harness)
    candidate = validate(candidate) if candidate is not None else None
    accepted = validate(accepted) if accepted is not None else None
    if accepted is not None and accepted["publisher"] != publisher:
        raise PreferenceError("accepted-publisher-mismatch")
    if local is not None:
        local = validate_local(local)
        if local["machine"].casefold() != machine.casefold():
            raise PreferenceError("local-machine-mismatch")
    status = "unaccepted"
    if candidate is None:
        status = "unavailable"
    elif candidate["publisher"] != publisher:
        status = "publisher-mismatch"
    elif conflict:
        status = "carrier-conflict"
    elif accepted is not None:
        if candidate["revision"] < accepted["revision"]:
            status = "older-revision"
        elif candidate["revision"] == accepted["revision"]:
            status = "unchanged" if digest(candidate) == digest(accepted) else "revision-conflict"
        elif candidate["parent"] != accepted["revision"]:
            status = "parent-gap"
        else:
            status = "pending"
    elif candidate["parent"] is not None:
        status = "parent-gap"
    usable = status in {"unaccepted", "pending", "unchanged"}
    snapshot = candidate if usable else accepted
    selected, provenance = {}, {}
    if snapshot is not None:
        _overlay(selected, snapshot["domains"], "shared", provenance)
        for identity, domains in snapshot.get("machines", {}).items():
            if identity.casefold() == machine.casefold():
                _overlay(selected, domains, "named-machine", provenance)
    if local is not None:
        _overlay(selected, local["domains"], "local-machine", provenance)
    mappings = selected.get("repositories", {}).get("knowledge", [])
    mapping = next((record for record in mappings if record["harness_repository_id"] == harness), None)
    return {
        "schema": "agent-machines.preferences.preview", "version": 1,
        "status": status, "candidate_eligible": usable,
        "received_revision": candidate["revision"] if candidate else None,
        "accepted_revision": accepted["revision"] if accepted else None,
        "projected_revision": snapshot["revision"] if snapshot else None,
        "candidate_sha256": digest(candidate) if candidate else None,
        "scope": {"machine": machine, "harness_repository_id": harness, "identity_resolution": "not-performed"},
        "values": selected, "provenance": provenance,
        "knowledge_preference": {
            "status": "absent" if mapping is None else
                      "suppressed" if mapping["knowledge_repository_id"] is None else "suggested",
            "knowledge_repository_id": mapping["knowledge_repository_id"] if mapping else None,
        },
        "adoption": {domain: "native-owner-required" for domain in selected},
        "configuration_applied": False, "binding_changed": False,
    }
