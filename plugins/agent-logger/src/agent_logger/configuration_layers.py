"""Ordinary organization preferences through the existing Worktrees read owner."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from configuration_context import context_from_owner, delivered_context, read_owner_context
from configuration_layers import compose

from . import _peer_launch


def _legacy_owner_prefix():
    from configuration_context import ready_owner_prefix

    from .repo_trust import _default_repos_yaml_root

    bootstrap = Path(sys.prefix) / ".runtime-bootstrap" / "versioned_runtime.py"
    if bootstrap.is_file() and not bootstrap.is_symlink():
        path = bootstrap
    else:
        payload = Path(__file__).resolve().parents[2]
        manifest = payload / "plugin.json"
        if not manifest.is_file():
            return []
        if json.loads(manifest.read_text(encoding="utf-8")).get("name") != "agent-logger":
            raise ValueError("configuration SDK belongs to another payload")
        path = payload / "scripts" / "versioned_runtime.py"
        if not path.is_file() or path.is_symlink():
            return []
    sdk = _peer_launch._load_primitive(path, "_logger_configuration_versioned_runtime")
    return ready_owner_prefix(
        _default_repos_yaml_root().absolute(), "agent_worktrees", resolver=sdk.resolve_python,
    )


def _owner_report(root: Path, home: Path):
    raw = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "").strip()
    own = _peer_launch.validate_owner("agent-logger", home, raw) if raw else None
    report = delivered_context(repository_root=root)
    if report is not None:
        reported_cell = context_from_owner(report)["installation_cell"]
        if own is not None and reported_cell != str(own["cellRoot"]):
            raise ValueError("configuration projection belongs to another installation cell")
        return report
    if raw:
        prefix = _peer_launch.launch_prefix(
            "agent-logger", Path(own["pluginRoot"]), raw, "agent-worktrees",
        )
    else:
        prefix = _legacy_owner_prefix()
    environment = None
    if not raw:
        from .repo_trust import _default_repos_yaml_root

        environment = _peer_launch.legacy_peer_environment(dict(os.environ))
        environment["AGENT_HOME"] = str(_default_repos_yaml_root().parent)
    return read_owner_context(prefix, root, required=bool(raw), environment=environment)


def _organization_file(root: Path):
    from .config import REPO_CONFIG_FILENAMES
    from .repo_trust import has_symlink_ancestor

    for name in REPO_CONFIG_FILENAMES:
        path = root / name
        if path.is_file() and not path.is_symlink() and not has_symlink_ancestor(root, path):
            return path
    return None


def load_organization_layers(root, base_path, base_data, machine_data, home):
    from .config import DEFAULTS, _deep_merge, _load_repo_config
    from .repo_trust import repo_config_is_trusted

    report = _owner_report(root, home)
    if report is None:
        return _deep_merge(_deep_merge(DEFAULTS, base_data), machine_data), base_data, {}
    context = context_from_owner(report)
    context["base_root"] = str(root.absolute())
    repository_id = context["repository_id"]
    machine = context["execution_machine"]
    cell = context["installation_cell"]
    layers = [{"id": "defaults", "tier": "defaults", "origin": "defaults", "values": DEFAULTS}]
    if base_path is not None:
        layers.append({
            "id": "repository", "tier": "repository", "origin": "base",
            "repository_id": repository_id, "installation_cell": cell, "values": base_data,
            "source": {"root": context["base_root"], "path": str(base_path.relative_to(root))},
        })
    repository_data = base_data
    if context["binding"] == "bound":
        knowledge = Path(context["knowledge_root"])
        if not repo_config_is_trusted(knowledge):
            raise ValueError("bound knowledge organization source is not trusted")
        path = _organization_file(knowledge)
        if path is not None:
            value = _load_repo_config(path)
            repository_data = _deep_merge(repository_data, value)
            layers.append({
                "id": "knowledge", "tier": "repository", "origin": "knowledge",
                "repository_id": repository_id, "installation_cell": cell, "values": value,
                "source": {"root": str(knowledge), "path": str(path.relative_to(knowledge))},
            })
    layers.append({
        "id": "machine", "tier": "machine", "origin": "local_machine",
        "execution_machine": machine, "installation_cell": cell, "values": machine_data,
        "source": {"root": str(home.absolute()), "path": "config.yaml"},
    })
    local = Path(report["repository_machine_root"])
    path = _organization_file(local)
    if path is not None:
        local_data = _load_repo_config(path)
        repository_data = _deep_merge(repository_data, local_data)
        layers.append({
            "id": "repository-machine", "tier": "repository_machine",
            "origin": "repository_machine", "repository_id": repository_id,
            "execution_machine": machine, "installation_cell": cell,
            "values": local_data,
            "source": {"root": str(local), "path": str(path.relative_to(local))},
        })
    result = compose(context, layers)
    return result["values"], repository_data, result["provenance"]
