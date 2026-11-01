"""Ordinary MCP timing preferences; credential/server/tool declarations remain native."""

from __future__ import annotations

import os
from pathlib import Path

import yaml
from configuration_context import context_from_owner, delivered_context, read_owner_context, ready_owner_prefix
from configuration_layers import compose, read_source

from . import _peer_launch
from .home import default_home_dir

FIELDS = {"timeout", "retries", "idle_timeout"}
RELPATH = Path(".copilot-extensions") / "agent-mcp" / "bridges"


def _legacy_report(root: Path) -> dict | None:
    own_root = Path(os.environ.get("AGENT_RT_ROOT") or default_home_dir())
    legacy = Path.home() / ".agent-mcp"
    peer = Path.home() / ".agent-worktrees"
    if own_root.resolve() != legacy.resolve() or not peer.is_dir():
        return None
    if (peer / ".installation-ownership.json").exists():
        raise _peer_launch.ContextRefused("legacy configuration owner has installation-cell ownership")
    path = own_root / "scripts" / "versioned_runtime.py"
    if not path.is_file():
        return None
    resolver = _peer_launch._load_primitive(path, "_mcp_configuration_runtime")
    prefix = ready_owner_prefix(peer, "agent_worktrees", resolver=resolver.resolve_python)
    if not prefix:
        return None
    environment = _peer_launch.legacy_peer_environment(dict(os.environ))
    environment.pop("AGENT_CONFIGURATION_CONTEXT", None)
    return read_owner_context(
        [prefix[0], "-I", "-X", "utf8", *prefix[1:]], root,
        environment=environment,
    )


def owner_report(root: Path) -> dict | None:
    raw = os.environ.get("COPILOT_EXTENSIONS_CONTEXT", "")
    own = None
    if raw:
        own_root = Path(os.environ.get("AGENT_RT_ROOT") or default_home_dir())
        own = _peer_launch.validate_owner("agent-mcp", own_root, raw)
    report = delivered_context(repository_root=root)
    if report is not None:
        return report
    if own is None:
        return _legacy_report(root)
    peer = Path(own["cellRoot"]) / "plugins" / "agent-worktrees"
    if not peer.exists() and not peer.is_symlink():
        return None
    return read_owner_context(
        _peer_launch.launch_prefix("agent-mcp", Path(own["pluginRoot"]), raw, "agent-worktrees"),
        root, required=True, environment=os.environ.copy(),
    )


def _ordinary(raw: dict) -> dict:
    values = {}
    for key, raw_value in raw.items():
        if key not in FIELDS:
            continue
        value = int(raw_value) if key == "retries" else float(raw_value)
        if key == "retries" and value < 0:
            raise ValueError("MCP ordinary retries must be a nonnegative integer")
        if key == "timeout" and value <= 0:
            raise ValueError("MCP ordinary timeout must be positive")
        values[key] = value
    return values


def _source(root: str, relative: Path) -> dict | None:
    if not Path(root).exists():
        return None
    for extension in (".yaml", ".yml", ".json"):
        document = read_source(root, str(relative) + extension, parser=yaml.safe_load)
        if document is not None:
            if not isinstance(document["values"], dict) or set(document["values"]) - FIELDS:
                raise ValueError("portable MCP timing overrides cannot change server, auth or tool policy")
            return document
    return None


def compose_preferences(data: dict, selected_path: Path, machine_values: dict) -> dict | None:
    from .config import _overlay_id

    report = owner_report(Path.cwd())
    if report is None:
        return None
    context = context_from_owner(report)
    bridge_id = _overlay_id(data, selected_path)
    # ID is a native selector, never a path fragment supplied unchecked.
    relative = RELPATH / bridge_id
    layers = [{
        "id": "mcp-defaults", "tier": "defaults", "origin": "defaults",
        "values": {"timeout": 30.0, "retries": 1, "idle_timeout": 300.0},
    }, {
        "id": "mcp-selected-declaration", "tier": "repository", "origin": "base",
        "repository_id": context["repository_id"], "values": _ordinary(data),
    }]
    sources = [(context["base_root"], "repository", "base")]
    if context["binding"] == "bound":
        sources.append((context["knowledge_root"], "repository", "knowledge"))
    if report.get("repository_machine_root"):
        sources.append((report["repository_machine_root"], "repository_machine", "repository_machine"))
    for root, tier, origin in sources:
        document = _source(root, relative)
        if document is None:
            continue
        layer = {
            "id": f"mcp-{origin}", "tier": tier, "origin": origin,
            "repository_id": context["repository_id"],
            "values": _ordinary(document["values"]), "source": document["source"],
        }
        if tier == "repository_machine":
            layer["execution_machine"] = context["execution_machine"]
            layer["installation_cell"] = context["installation_cell"]
        layers.append(layer)
    layers.append({
        "id": "mcp-native-machine-override", "tier": "machine", "origin": "local_machine",
        "execution_machine": context["execution_machine"],
        "installation_cell": context["installation_cell"], "values": _ordinary(machine_values),
    })
    result = compose(context, layers)
    # Invocation environment remains outside the portable tiers.
    if "idle_timeout" not in data and not any(
        "idle_timeout" in layer["values"] for layer in layers[2:]
    ) and "AGENT_MCP_BRIDGE_IDLE_TIMEOUT" in os.environ:
        result["values"].pop("idle_timeout", None)
        result["provenance"].pop("/idle_timeout", None)
        result["native_selectors"] = {"idle_timeout": "AGENT_MCP_BRIDGE_IDLE_TIMEOUT"}
    return result
