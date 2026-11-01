"""Ordinary spawn defaults; native bindings, session policy and custody stay separate."""

from __future__ import annotations

import os
import json
from pathlib import Path

import yaml
from configuration_context import context_from_owner, delivered_context, read_owner_context
from configuration_layers import ConfigurationError, compose, read_source

FIELDS = {"default_copilot_args", "default_env"}
PROTECTED_ENV = {
    "AGENT_BRIDGE_PREFERENCE_SOURCE", "AGENT_BRIDGE_PREFERENCE_CONTEXT",
    "AGENT_CONFIGURATION_CONTEXT", "COPILOT_EXTENSIONS_CONTEXT", "AGENT_RT_ROOT",
    "GH_TOKEN", "GITHUB_TOKEN", "AGENT_VAULT_PINNED_CONTEXT",
}


def _parse_source(text: str):
    value = yaml.safe_load(text)
    return {} if value is None else value


def _ordinary(values: dict, *, portable=False) -> dict:
    projected = {key: value for key, value in values.items() if key in FIELDS}
    if "default_env" in projected:
        environment = projected["default_env"]
        if portable and set(environment) & PROTECTED_ENV:
            raise ValueError("portable bridge environment cannot override preference or installation authority")
        projected["default_env"] = {
            key: value for key, value in environment.items() if key not in PROTECTED_ENV
        }
    return projected


def _checked_report(report, own):
    if report is not None and own is not None and (
        context_from_owner(report)["installation_cell"] != str(own["cellRoot"])
    ):
        raise ConfigurationError("Bridge configuration belongs to another installation cell")
    return report


def owner_report(root: Path) -> dict | None:
    from .session_lifecycle_cli import (
        _agent_bridge_installation_context,
        _agent_bridge_owner_root,
        _agent_worktrees_environment,
        _agent_worktrees_launch_prefix,
    )
    from ._peer_launch import validate_owner

    raw = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    # The pre-cell reader accepts an in-memory marketplace selector, not an
    # installation receipt. Keep that existing source-only form source-only.
    if raw.lstrip().startswith("{"):
        try:
            selector = json.loads(raw)
        except ValueError:
            selector = None
        from .install_paths import install_dir, uses_legacy_install_dir

        if (
            isinstance(selector, dict) and set(selector) == {"marketplaceId"}
            and uses_legacy_install_dir(install_dir())
        ):
            return delivered_context(repository_root=root)
    raw = _agent_bridge_installation_context()
    own = validate_owner("agent-bridge", _agent_bridge_owner_root(), raw) if raw else None
    prefix = _agent_worktrees_launch_prefix(allow_path_fallback=False)
    report = delivered_context(repository_root=root)
    if report is not None:
        return _checked_report(report, own)
    return _checked_report(read_owner_context(
        prefix, root, required=bool(prefix and own is not None),
        environment=_agent_worktrees_environment(),
    ), own)


def ordinary_layers(root: Path) -> tuple[dict, list[dict]] | None:
    from .config import _repo_config_layers
    from .models import RepoBridgeConfig

    report = owner_report(root)
    if report is None:
        return None
    context = context_from_owner(report)
    layers = [{
        "id": "bridge-defaults", "tier": "defaults", "origin": "defaults",
        "values": {"default_copilot_args": [], "default_env": {}},
    }]
    roots = [(context["base_root"], "base", "repository")]
    if context["binding"] == "bound":
        roots.append((context["knowledge_root"], "knowledge", "repository"))
    if report.get("repository_machine_root"):
        roots.append((
            report["repository_machine_root"], "repository_machine", "repository_machine",
        ))
    for source_root, origin, tier in roots:
        for index, path in enumerate(_repo_config_layers(Path(source_root))):
            document = read_source(
                source_root, str(path.relative_to(source_root)),
                parser=_parse_source, optional=False,
            )
            values = document["values"]
            values = {} if values is None else values
            if not isinstance(values, dict):
                raise ValueError("bridge ordinary source must be a mapping")
            if origin != "base" and set(values) - FIELDS:
                raise ValueError("portable bridge overrides support ordinary spawn defaults only")
            if "preference_source" in values:
                raise ValueError("repo preference policy belongs in default_env")
            RepoBridgeConfig(**values)
            layer = {
                "id": f"bridge-{origin}-{index}", "tier": tier, "origin": origin,
                "repository_id": context["repository_id"],
                "values": _ordinary(values, portable=origin != "base"),
                "source": document["source"],
            }
            if tier == "repository_machine":
                layer["execution_machine"] = context["execution_machine"]
                layer["installation_cell"] = context["installation_cell"]
            layers.append(layer)
    # Validate context and sources even when every optional document is absent.
    compose(context, layers)
    return context, layers


def effective_spawn_defaults(profile, repo_config) -> tuple[list[str], dict[str, str]] | None:
    selected = getattr(repo_config, "_ordinary_layers", None)
    if selected is None:
        return None
    context, layers = selected
    values = {
        field: getattr(profile, field)
        for field in FIELDS if field in getattr(profile, "model_fields_set", set())
    }
    result = compose(context, [*layers, {
        "id": "bridge-machine-profile", "tier": "machine", "origin": "local_machine",
        "execution_machine": context["execution_machine"],
        "installation_cell": context["installation_cell"], "values": _ordinary(values),
    }])
    from .models import RepoBridgeConfig

    fields = getattr(profile, "model_fields_set", set())
    protected = profile.default_env if "default_env" in fields else repo_config.default_env
    result["values"]["default_env"].update({
        key: value for key, value in protected.items() if key in PROTECTED_ENV
    })
    validated = RepoBridgeConfig(**result["values"])
    object.__setattr__(repo_config, "_configuration_provenance", result["provenance"])
    return list(validated.default_copilot_args), dict(validated.default_env)
