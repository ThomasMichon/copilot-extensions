"""Versioned read-only proof of configured execution identity."""

from __future__ import annotations

import json

import yaml
from machine_transport import find_machine_entry

from . import config as cfg
from .execution_spaces import (
    ExecutionSpaceError, _require_registered_current_space, _requires_scoped_identity, registry_for_config,
)

SCHEMA = "agent-worktrees.execution-identity"
VERSION = 1
CONFIGURED_SOURCES = {"project-machine-config", "global-machine-config"}


def read_execution_identity() -> dict:
    """Read fresh configuration; never turn detected hostname into authority."""
    result = {"schema": SCHEMA, "version": VERSION, "status": "unavailable"}
    project = cfg.active_project()
    if not project:
        return {**result, "reason": "project-not-registered"}
    binding: dict[str, str] = {}
    try:
        with cfg.inrepo_config_source.resolution_budget():
            config = cfg._load_config_uncached(
                include_control_plane_related_pr=False, project=project,
                _machine_binding=binding,
            )
    except cfg.ProjectNotRegisteredError:
        return {**result, "project": project, "reason": "project-not-registered"}
    except ExecutionSpaceError:
        if binding.get("source") == "detected":
            return {**result, "project": project, "reason": "machine-binding-not-configured"}
        raise
    source = binding.get("source", "detected")
    result.update(project=config.repo_name, source=source)
    if source not in CONFIGURED_SOURCES:
        return {**result, "reason": "machine-binding-not-configured"}
    entries = registry_for_config(config)
    current = find_machine_entry(entries, config.machine, reject_ambiguous=True)
    if _requires_scoped_identity(entries, config.machine):
        platform = _require_registered_current_space(config, current)
    else:
        platform = cfg.detect_platform()
        if config.platform != platform:
            raise ExecutionSpaceError("configured platform does not match the current execution platform")
    machine = current.key if current is not None else config.machine
    if not isinstance(machine, str) or not machine:
        raise ExecutionSpaceError("configured machine binding is invalid")
    return {
        **result, "status": "ready", "configured": True,
        "machine": machine, "platform": platform,
    }


def cmd_get_execution_identity() -> int:
    """Emit a compatible refusal distinctly from an unavailable capability."""
    try:
        result = read_execution_identity()
    except (OSError, ValueError, RuntimeError, KeyError, TypeError, AttributeError, yaml.YAMLError) as exc:
        result = {
            "schema": SCHEMA, "version": VERSION, "status": "refused",
            "reason": str(exc),
        }
    print(json.dumps(result))
    return 1 if result["status"] == "refused" else 0
