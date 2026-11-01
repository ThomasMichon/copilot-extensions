"""Read-only presentation preferences, never SSH profile or topology authority."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from configuration_context import context_from_owner, delivered_context, read_owner_context, ready_owner_prefix
from configuration_layers import ConfigurationError, compose, read_source

from . import _peer_launch, _worktrees

FIELDS = {"display_name", "description", "capabilities"}
RELPATH = Path(".copilot-extensions") / "agent-ssh" / "config.yaml"


def _legacy_report(root: Path) -> tuple[dict, Path] | None:
    legacy = Path.home() / ".agent-ssh"
    own_root = Path(os.environ.get("AGENT_RT_ROOT") or legacy)
    peer = Path.home() / ".agent-worktrees"
    if own_root.resolve() != legacy.resolve() or not peer.is_dir():
        return None
    if (peer / ".installation-ownership.json").exists():
        raise _peer_launch.ContextRefused("legacy configuration owner has installation-cell ownership")
    path = own_root / "scripts" / "versioned_runtime.py"
    if not path.is_file():
        return None
    resolver = _peer_launch._load_primitive(path, "_ssh_configuration_runtime")
    prefix = ready_owner_prefix(peer, "agent_worktrees", resolver=resolver.resolve_python)
    if not prefix:
        return None
    environment = _peer_launch.legacy_peer_environment(dict(os.environ))
    environment.pop("AGENT_CONFIGURATION_CONTEXT", None)
    report = read_owner_context(
        [prefix[0], "-I", "-X", "utf8", *prefix[1:]], root,
        environment=environment,
    )
    return (report, legacy) if report is not None else None


def owner_report(root: Path) -> tuple[dict, Path] | None:
    own = _worktrees.validate_context()
    report = delivered_context(repository_root=root)
    if report is not None:
        if own is not None and context_from_owner(report)["installation_cell"] != str(own["cellRoot"]):
            raise ConfigurationError("SSH configuration belongs to another installation cell")
        if own is None:
            fresh = _legacy_report(root)
            if fresh is None or any(
                context_from_owner(fresh[0])[key] != context_from_owner(report)[key]
                for key in ("repository_id", "installation_cell")
            ):
                raise ConfigurationError("delivered configuration has no matching qualified owner")
            return fresh
        machine_root = Path(own["pluginRoot"]) if own else (
            Path.home() / ".agent-ssh"
        )
        return report, machine_root
    if own is None:
        return _legacy_report(root)
    peer = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
    try:
        peer.lstat()
    except FileNotFoundError:
        return None
    except OSError as error:
        raise ConfigurationError("optional Worktrees installation could not be inspected") from error
    prefix = _worktrees.launch_prefix(
        "agent-ssh", Path(own["pluginRoot"]),
        os.environ.get("COPILOT_EXTENSIONS_CONTEXT") or str(Path(own["pluginRoot"]) / "install.json"),
        "agent-worktrees",
    )
    report = read_owner_context(prefix, root, environment=os.environ.copy())
    if report is not None and context_from_owner(report)["installation_cell"] != str(own["cellRoot"]):
        raise ConfigurationError("SSH configuration belongs to another installation cell")
    return (report, Path(own["pluginRoot"])) if report is not None else None


def _values(raw: object) -> dict:
    if not isinstance(raw, dict) or set(raw) - {"machines"}:
        raise ValueError("SSH presentation config supports only machines")
    machines = raw.get("machines", {})
    if not isinstance(machines, dict):
        raise ValueError("SSH presentation machines must be a mapping")
    for key, value in machines.items():
        if not isinstance(key, str) or not isinstance(value, dict) or set(value) - FIELDS:
            raise ValueError("SSH presentation cannot override topology or transport fields")
        for field, item in value.items():
            if field == "capabilities":
                if not isinstance(item, list) or any(not isinstance(entry, str) for entry in item):
                    raise ValueError("SSH capabilities must be a string list")
            elif not isinstance(item, str):
                raise ValueError("SSH presentation labels must be strings")
    return raw


def apply_preferences(mesh, root: Path):
    selected = owner_report(root)
    if selected is None:
        return mesh
    report, machine_root = selected
    context = context_from_owner(report)
    layers = [{
        "id": "ssh-defaults", "tier": "defaults", "origin": "defaults",
        "values": {"machines": {
            item.key: {"display_name": "", "description": "", "capabilities": []}
            for item in mesh.machines
        }},
    }, {
        "id": "ssh-declared-metadata", "tier": "repository", "origin": "base",
        "repository_id": context["repository_id"],
        "values": {"machines": {
            item.key: {field: getattr(item, field) for field in FIELDS}
            for item in mesh.machines
        }},
    }]
    sources = [(context["base_root"], RELPATH, "repository", "base")]
    if context["binding"] == "bound":
        sources.append((context["knowledge_root"], RELPATH, "repository", "knowledge"))
    sources.append((str(machine_root), Path("config.yaml"), "machine", "local_machine"))
    if report.get("repository_machine_root"):
        sources.append((
            report["repository_machine_root"], RELPATH, "repository_machine", "repository_machine",
        ))
    for source_root, relative, tier, origin in sources:
        if not Path(source_root).exists():
            continue
        document = read_source(source_root, str(relative), parser=yaml.safe_load)
        if document is None:
            continue
        layer = {
            "id": f"ssh-{origin}", "tier": tier, "origin": origin,
            "values": _values(document["values"]), "source": document["source"],
        }
        if tier in {"repository", "repository_machine"}:
            layer["repository_id"] = context["repository_id"]
        if tier in {"machine", "repository_machine"}:
            layer["execution_machine"] = context["execution_machine"]
            layer["installation_cell"] = context["installation_cell"]
        layers.append(layer)
    result = compose(context, layers)
    for machine in mesh.machines:
        for field, value in result["values"]["machines"][machine.key].items():
            setattr(machine, field, value)
    mesh.configuration_provenance = result["provenance"]
    return mesh
