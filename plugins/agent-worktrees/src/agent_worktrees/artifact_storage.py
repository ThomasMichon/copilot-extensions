"""Inert storage-policy composition for owner-selected artifact sources."""

from __future__ import annotations

import copy
import hashlib
import os
from pathlib import Path
import tempfile
from typing import Any, Mapping

from configuration_context import context_from_owner
from configuration_layers import ConfigurationError, compose, read_source

POLICY_VERSION = 1
DEFAULT_DIAGNOSTIC_FILES = 16
DEFAULT_DIAGNOSTIC_BYTES = 8 * 1024 * 1024
_POLICY_KEYS = {"scratch_root", "failure_evidence"}
_EVIDENCE_KEYS = {"max_files", "max_bytes"}
DOCUMENT = ".copilot-extensions/artifact-storage.json"


def validate_values(values: object, *, machine_paths: bool) -> dict[str, Any]:
    """Validate each contributing policy, not just its surviving merged fields."""
    if not isinstance(values, dict) or set(values) - _POLICY_KEYS:
        raise ConfigurationError("unknown artifact storage policy field")
    if "scratch_root" in values:
        root = values["scratch_root"]
        if not machine_paths:
            raise ConfigurationError("physical scratch roots require a machine-scoped source")
        if not isinstance(root, str) or not root or "\x00" in root or not Path(root).is_absolute():
            raise ConfigurationError("scratch_root must name an absolute native directory")
    if "failure_evidence" in values:
        limits = values["failure_evidence"]
        if not isinstance(limits, dict) or set(limits) - _EVIDENCE_KEYS:
            raise ConfigurationError("unknown failure evidence policy field")
        for value in limits.values():
            if type(value) is not int or not 1 <= value <= 9007199254740991:
                raise ConfigurationError("failure evidence limits must be positive portable integers")
    return copy.deepcopy(values)


def resolve_storage(
    context: dict[str, Any],
    layers: list[dict[str, Any]],
    *,
    explicit_root: str | None = None,
    environment: Mapping[str, str] | None = None,
    temporary_root: Path | None = None,
    session_root: Path | None = None,
) -> dict[str, Any]:
    """Resolve data only; callers still own root admission, allocation and custody.

    Layers come from the existing configuration owner with exact repository,
    machine and source identity. This function performs no source discovery,
    file allocation, registry read or permission mutation.
    """
    selected = []
    for layer in layers:
        if not isinstance(layer, dict):
            raise ConfigurationError("artifact storage layer must be an object")
        values = validate_values(
            layer.get("values"),
            machine_paths=layer.get("tier") in {"machine", "repository_machine"},
        )
        selected.append({**layer, "values": values})
    defaults = {
        "id": "artifact-storage-defaults", "tier": "defaults", "origin": "defaults",
        "values": {
            "failure_evidence": {
                "max_files": DEFAULT_DIAGNOSTIC_FILES,
                "max_bytes": DEFAULT_DIAGNOSTIC_BYTES,
            },
        },
    }
    result = compose(context, [defaults, *selected])
    values = result["values"]
    env = os.environ if environment is None else environment
    override = explicit_root if explicit_root is not None else env.get("AGENT_SCRATCH_ROOT")
    root_origin = "operation" if explicit_root is not None else "environment"
    if override is not None:
        root = validate_values({"scratch_root": override}, machine_paths=True)["scratch_root"]
    elif "scratch_root" in values:
        root = values["scratch_root"]
        root_origin = result["provenance"]["/scratch_root"]["id"]
    else:
        parent = temporary_root if temporary_root is not None else Path(tempfile.gettempdir())
        if not parent.is_absolute():
            raise ConfigurationError("OS temporary root must be absolute")
        suffix = (
            str(os.getuid()) if hasattr(os, "getuid")
            else hashlib.sha256(str(Path.home()).encode("utf-8")).hexdigest()[:12]
        )
        root = str(parent / f"agent-scratch-{suffix}")
        root_origin = "private-os-temp"
    path = Path(os.path.abspath(root))
    forbidden = [
        Path(context[key]) for key in ("base_root", "knowledge_root")
        if context.get(key) is not None
    ]
    if session_root is not None:
        forbidden.append(session_root)
    for owner_root in forbidden:
        if path.is_relative_to(owner_root.absolute()) or path.resolve().is_relative_to(owner_root.resolve()):
            raise ConfigurationError("scratch root must be outside source and session-state homes")
    return {
        "schema": "copilot-extensions.artifact-storage", "version": POLICY_VERSION,
        "scratch_root": str(path),
        "failure_evidence": copy.deepcopy(values["failure_evidence"]),
        "provenance": copy.deepcopy(result["provenance"]),
        "scratch_root_origin": root_origin,
        "layers": list(result["layers"]),
        "root_admitted": False,
    }


def load_storage(report: dict[str, Any], **options: Any) -> dict[str, Any]:
    """Read only exact owner-selected storage documents; never scan for a root."""
    context = context_from_owner(report)
    layers: list[dict[str, Any]] = []
    for origin, key in (("base", "base_root"), ("knowledge", "knowledge_root")):
        root = context.get(key)
        if root is None:
            continue
        document = read_source(root, DOCUMENT)
        if document is None:
            continue
        body = document["values"]
        allowed = {"version", "portable"} | ({"machines"} if origin == "knowledge" else set())
        if set(body) - allowed or type(body.get("version")) is not int or body["version"] != POLICY_VERSION:
            raise ConfigurationError("unsupported artifact storage document")
        portable = validate_values(body.get("portable", {}), machine_paths=False)
        layers.append({
            "id": f"artifact-storage-{origin}", "tier": "repository", "origin": origin,
            "repository_id": context["repository_id"], "values": portable,
            "source": document["source"],
        })
        machines = body.get("machines", {})
        if not isinstance(machines, dict):
            raise ConfigurationError("storage machines must be a mapping")
        for machine, values in machines.items():
            if not isinstance(machine, str) or not machine:
                raise ConfigurationError("storage machine name must be nonempty text")
            validated = validate_values(values, machine_paths=True)
            layers.append({
                "id": f"artifact-storage-machine:{machine}", "tier": "machine",
                "origin": "named_machine", "execution_machine": machine,
                "values": validated, "source": document["source"],
            })
    machine_root = report.get("repository_machine_root")
    if machine_root is not None:
        document = read_source(machine_root, "artifact-storage.json")
        if document is not None:
            body = document["values"]
            if set(body) != {"version", "values"} or type(body["version"]) is not int or body["version"] != POLICY_VERSION:
                raise ConfigurationError("unsupported machine-local artifact storage document")
            layers.append({
                "id": "artifact-storage-local", "tier": "repository_machine",
                "origin": "repository_machine",
                "repository_id": context["repository_id"],
                "execution_machine": context["execution_machine"],
                "values": validate_values(body["values"], machine_paths=True),
                "source": document["source"],
            })
    return resolve_storage(context, layers, **options)
