"""Ordinary Index configuration; activation and installation policy stay native."""

from __future__ import annotations

import copy
import json
import os
import sys
from pathlib import Path

from configuration_context import context_from_owner, delivered_context, read_owner_context
from configuration_layers import compose, read_source

from . import config as cfg


def owner_report(root, *, state_policy=None):
    raw = os.environ.get(cfg.INSTALLATION_CONTEXT_ENV, "").strip()
    policy, _external = (state_policy or cfg._requires_external)(root)
    if policy != "ready":
        raise ValueError("repository state policy is invalid")
    own = None
    if raw:
        own = cfg._knowledge_overlay._validate_index_owner_or_refuse(
            raw, cfg._load_installation_context,
        )
    else:
        installation = cfg.install_dir().absolute()
        if (
            installation.name == "agent-index" and installation.parent.name == "plugins"
            and installation.parent.parent.parent.name == "marketplaces"
        ):
            raw = str(installation / "install.json")
            peer = cfg._knowledge_overlay._load_peer_launch()
            own = peer.validate_owner("agent-index", installation, raw)
    report = delivered_context(repository_root=root)
    if report is not None:
        reported_cell = context_from_owner(report)["installation_cell"]
        if own is not None and reported_cell != str(own["cellRoot"]):
            raise ValueError("configuration projection belongs to another installation cell")
    if raw:
        peer = cfg._knowledge_overlay._load_peer_launch()
        receipt = Path(own["cellRoot"]) / "plugins" / "agent-worktrees" / "install.json"
        prefix = peer.launch_prefix(
            "agent-index", Path(own["pluginRoot"]), raw, "agent-worktrees",
        ) if receipt.exists() or receipt.is_symlink() else []
    else:
        from configuration_context import ready_owner_prefix

        prefix = _legacy_owner_prefix(ready_owner_prefix)
    environment = None
    if not raw:
        peer = cfg._knowledge_overlay._load_peer_launch()
        environment = peer.legacy_peer_environment(dict(os.environ))
        environment["AGENT_HOME"] = str(cfg._agent_worktrees_home().parent)
    fresh = read_owner_context(
        prefix, root, required=report is not None, environment=environment,
    )
    if fresh is not None:
        context = context_from_owner(fresh)
        if own is not None and context["installation_cell"] != str(own["cellRoot"]):
            raise ValueError("configuration owner belongs to another installation cell")
        if report is not None and (
            context["repository_id"] != context_from_owner(report)["repository_id"]
            or context["installation_cell"] != context_from_owner(report)["installation_cell"]
        ):
            raise ValueError("configuration projection differs from the current owner")
    return fresh


def _legacy_owner_prefix(ready_owner_prefix):
    bootstrap = Path(sys.prefix) / ".runtime-bootstrap" / "versioned_runtime.py"
    if bootstrap.is_file() and not bootstrap.is_symlink():
        path = bootstrap
    else:
        payload = Path(__file__).resolve().parents[2]
        manifest = payload / "plugin.json"
        if not manifest.is_file():
            return []
        if json.loads(manifest.read_text(encoding="utf-8")).get("name") != "agent-index":
            raise ValueError("configuration SDK belongs to another payload")
        path = payload / "scripts" / "versioned_runtime.py"
        if not path.is_file() or path.is_symlink():
            return []
    peer = cfg._knowledge_overlay._load_peer_launch()
    sdk = peer._load_primitive(path, "_index_configuration_versioned_runtime")
    return ready_owner_prefix(
        cfg._agent_worktrees_home().absolute(), "agent_worktrees", resolver=sdk.resolve_python,
    )


def _repository_paths(root):
    return [
        root / cfg.REPO_CONFIG_RELPATH,
        root / cfg.LEGACY_REPO_CONFIG_RELPATH,
    ]


def load_repository_layers(root, *, parser=None, state_policy=None):
    report = owner_report(root, state_policy=state_policy)
    if report is None:
        # Optional absent/older owner retains native files without a second
        # binding lookup through a legacy or cross-cell front door.
        merged = {}
        paths = _repository_paths(root)
        marketplace = cfg._repo_marketplace_overlay_path(root)
        if marketplace is not None:
            paths.append(marketplace)
        for path in paths:
            if parser is None:
                value = cfg._load_yaml(path)
            else:
                source = read_source(str(root), str(path.relative_to(root)), parser=parser)
                value = source["values"] if source is not None else {}
            merged = cfg._merge_repo_config_dicts(merged, value)
        return {"values": merged, "provenance": {}, "layers": []}
    context = context_from_owner(report)
    context["base_root"] = str(root.absolute())
    layers = [{"id": "defaults", "tier": "defaults", "origin": "defaults", "values": {}}]
    merged = {}

    def append(owner, path, tier, origin):
        nonlocal merged
        if not owner.exists() and tier in ("machine", "repository_machine"):
            return
        state, safe = cfg._knowledge_overlay._safe_file(path, owner)
        if state == "absent":
            return
        if state != "ready" or safe is None:
            raise ValueError("selected Index configuration source is unsafe")
        source = read_source(
            str(owner), str(path.relative_to(owner)),
            parser=parser or (lambda text: __import__("yaml").safe_load(text) or {}),
        )
        if source is None:
            return
        value = copy.deepcopy(source["values"])
        if "indexers" in value and "indexer" not in value:
            value["indexer"] = None
        elif "indexer" in value and "indexers" not in value:
            value["indexers"] = None
        old = merged.get("corpus")
        new = value.get("corpus")
        if isinstance(new, dict) and isinstance(new.get("sources"), list):
            for item in new["sources"]:
                if isinstance(item, dict):
                    item["_repo_path"] = str(owner)
                    item["_contributed_by"] = origin
        if isinstance(old, dict) and isinstance(new, dict):
            if isinstance(old.get("sources"), list) and isinstance(new.get("sources"), list):
                new["sources"] = cfg._merge_sources_by_precedence(new["sources"], old["sources"])
        layer = {
            "id": f"{origin}:{path}", "tier": tier, "origin": origin,
            "installation_cell": context["installation_cell"], **source, "values": value,
        }
        if tier in ("repository", "repository_machine"):
            layer["repository_id"] = context["repository_id"]
        if tier in ("machine", "repository_machine"):
            layer["execution_machine"] = context["execution_machine"]
        layers.append(layer)
        merged = cfg._merge_repo_config_dicts(merged, value)

    for path in _repository_paths(root):
        append(root, path, "repository", "base")
    marketplace = cfg._repo_marketplace_overlay_path(root)
    if marketplace is not None:
        append(root, marketplace, "repository", "base")
    if context["binding"] == "bound":
        knowledge = Path(context["knowledge_root"])
        for path in _repository_paths(knowledge):
            append(knowledge, path, "repository", "knowledge")
    machine_path = cfg.config_path().absolute()
    append(machine_path.parent, machine_path, "machine", "local_machine")
    local = Path(report["repository_machine_root"])
    for path in _repository_paths(local):
        append(local, path, "repository_machine", "repository_machine")
    result = compose(context, layers)
    for key in ("indexer", "indexers"):
        if result["values"].get(key) is None:
            result["values"].pop(key, None)
    return result


def load_repository_candidate(root, native, *, knowledge_root=None, compose_preferences=True):
    """Keep native script readers and validators selected in their own namespace."""
    layers = native["_repo_config_layers"](root, knowledge_root=knowledge_root)
    if not layers:
        return "absent", None, None
    merged = {}
    for owner, layer in layers:
        state, safe_path = native["_safe_file"](layer, owner)
        if state != "ready" or safe_path is None:
            return state, layer, None
        state, data = native["_load_yaml_mapping"](safe_path)
        if state != "ready" or data is None:
            return state, layer, None
        merged = native["_merge_repo_config_dicts"](merged, data)
    try:
        primary = native["_primary_repo_config_path"](root) or layers[0][1]
        native["_validate_config"](merged)  # Native activation veto precedes preferences.
        if compose_preferences:
            layered = load_repository_layers(
                root, parser=native["_parse_yaml_for_layers"], state_policy=native["_requires_external"],
            )
            if layered is not None:
                merged = layered["values"]
        return "ready", primary.resolve(), native["_validate_config"](merged)
    except (ValueError, RuntimeError):
        primary = native["_primary_repo_config_path"](root) or layers[0][1]
        return "invalid", primary, None


def knowledge_root_from_owner(root):
    try:
        report = owner_report(root)
        if report is None:
            return "unavailable", None
        context = context_from_owner(report)
        if context["binding"] != "bound":
            return "unavailable", None
        return "ready", Path(context["knowledge_root"])
    except (ValueError, RuntimeError):
        return "invalid", None


def external_configuration_result(root, external_path, native):
    """Apply preferences only after the caller qualified the external candidate."""
    try:
        layered = load_repository_layers(
            root, parser=native["_parse_yaml_for_layers"], state_policy=native["_requires_external"],
        )
        if layered is None:
            raise native["ConfigError"]("configuration owner became unavailable")
        external = native["_validate_config"](layered["values"])
    except (ValueError, RuntimeError):
        return native["_result"](
            opted_in=False, source="external-state-root",
            reason="external-state-root-config-invalid", config=external_path,
            repo_root=root, requires_external=True,
        )
    return native["_result"](
        opted_in=True, source="external-state-root", reason="external-state-root-config",
        config=external_path, repo_root=root, requires_external=True, normalized=external,
    )
