"""Model-routing's native entry replacement over the owner-delivered data plan."""

from __future__ import annotations

import importlib.util
import os
import sys
from pathlib import Path


def _load_owned_module(path, name):
    if not path.is_file() or path.is_symlink():
        raise ValueError("qualified owner SDK is unavailable")
    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ValueError("qualified owner SDK cannot be loaded")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    old = sys.dont_write_bytecode
    try:
        sys.dont_write_bytecode = True
        spec.loader.exec_module(module)
    finally:
        sys.dont_write_bytecode = old
    return module


def _installation_sdk(payload):
    return _load_owned_module(
        payload / "scripts" / "installation-context" / "installation_context.py",
        "_conduct_configuration_installation",
    )


def _qualified_peer_payload(sdk, own, home, repo, environment):
    installed = home / ".copilot" / "installed-plugins"
    locator = own.get("locator") or {}
    keys = (
        [locator["marketplaceKey"]] if locator.get("kind") == "installed"
        else [path.name for path in installed.iterdir() if path.is_dir()]
        if installed.is_dir() else []
    )
    matches = []
    for key in keys:
        candidate = installed / key / "agent-worktrees"
        if not candidate.is_dir():
            continue
        peer = sdk.resolve_payload_source(
            payload_root=candidate, plugin_id="agent-worktrees",
            copilot_home=home / ".copilot", project_root=repo, environment=environment,
        )
        if peer["sourceFingerprint"] == own["sourceFingerprint"]:
            matches.append(peer)
    if len(matches) != 1:
        raise ValueError("one qualified installed configuration owner is required")
    return matches[0]


def _ready_owner_prefix(payload, runtime_root):
    from configuration_context import ready_owner_prefix

    runtime = _load_owned_module(
        payload / "scripts" / "versioned_runtime.py", "_conduct_configuration_versioned_runtime",
    )
    return ready_owner_prefix(runtime_root, "agent_worktrees", resolver=runtime.resolve_python)


def qualified_owner_report(repo, payload, home, environment):
    """Reacquire actual owner mode and configuration; placement is not activation."""
    from configuration_context import context_from_owner, read_owner_context

    sdk = _installation_sdk(payload)
    own = sdk.resolve_payload_source(
        payload_root=payload, plugin_id="agent-conduct-guidance",
        copilot_home=home / ".copilot", project_root=repo, environment=environment,
    )
    source_environment = dict(environment)
    source_environment.pop("COPILOT_PLUGIN_ROOT", None)
    peer = _qualified_peer_payload(sdk, own, home, repo, source_environment)
    peer_payload = Path(peer["payloadRoot"])
    registry = _load_owned_module(
        peer_payload / "scripts" / "registry_root.py", "_conduct_configuration_registry",
    )
    legacy_root = registry._legacy_home(environment) / ".agent-worktrees"
    mode_environment = dict(source_environment)
    mode_environment["COPILOT_PLUGIN_ROOT"] = str(peer_payload)
    mode = sdk.resolve_runtime_deployment(
        legacy_root=legacy_root, payload_root=peer_payload, plugin_id="agent-worktrees",
        copilot_home=home / ".copilot", project_root=repo,
        expected_marketplace_id=peer["marketplaceId"], environment=mode_environment,
    )
    if (
        mode["status"] != "ready" or mode["reason"] == "activation-required"
        or mode["actualMode"] not in {"legacy", "namespaced"} or not mode["runtimeRoot"]
        or mode["deployment"]["state"] not in {"missing", "valid"}
    ):
        raise ValueError("configuration owner mode is unavailable or refused")
    boundary = _load_owned_module(
        peer_payload / "src" / "agent_worktrees" / "_peer_launch.py",
        "_conduct_configuration_peer_environment",
    )
    runtime_root = Path(mode["runtimeRoot"])
    if mode["actualMode"] == "namespaced":
        context = sdk.resolve_context(
            context=mode["context"], plugin_id="agent-worktrees", payload_root=peer_payload,
            copilot_home=home / ".copilot", project_root=repo,
            expected_marketplace_id=peer["marketplaceId"], environment=mode_environment,
        )
        namespace = sdk.validate_namespace_receipt(context["namespaceReceipt"], home / ".copilot-extensions")
        if (
            context["state"] != "active" or namespace["receipt"]["state"] != "active"
            or not sdk.paths_equal(context["pluginRoot"], runtime_root)
        ):
            raise ValueError("configuration owner is not currently active")
        expected_cell = context["cellRoot"]
        child_environment = boundary.peer_environment(context, dict(environment))
    else:
        expected_cell = str(runtime_root)
        child_environment = boundary.legacy_peer_environment(dict(environment))
        child_environment["AGENT_HOME"] = str(runtime_root.parent)
    prefix = _ready_owner_prefix(peer_payload, runtime_root)
    report = read_owner_context(prefix, repo, required=True, environment=child_environment)
    if not sdk.paths_equal(context_from_owner(report)["installation_cell"], expected_cell):
        raise ValueError("configuration owner report belongs to another current cell")
    return report


def compose_routing(
    repo, home, repository, operator, operator_status, load, trusted, diagnostics, merge,
):
    if not os.environ.get("AGENT_CONFIGURATION_CONTEXT"):
        return merge(repository, operator), {}, {}
    # Parent-owned release materialization places the canonical stdlib library here.
    bundled = Path(__file__).parent / "configuration-layers" / "src"
    if bundled.is_dir():
        sys.path.insert(0, str(bundled))
    from configuration_context import context_from_owner, delivered_context
    from configuration_layers import compose

    report = delivered_context(repository_root=repo)
    if report is None:
        return merge(repository, operator), {}, {}
    original = context_from_owner(report)
    owner = qualified_owner_report(repo, Path(__file__).parent.parent, home, dict(os.environ))
    context = context_from_owner(owner)
    if original["installation_cell"] != context["installation_cell"]:
        raise ValueError("configuration projection belongs to another current cell")
    if original["repository_id"] != context["repository_id"]:
        raise ValueError("configuration projection belongs to another repository")
    context["base_root"] = str(repo)
    statuses = {}
    layers = [{"id": "defaults", "tier": "defaults", "origin": "defaults", "values": {}}]

    def append(layer_id, tier, origin, source, path, root):
        # Native routing replaces an entire model entry, never its individual
        # evidence/state fields. Single-element atomic lists encode that rule.
        values = {
            purpose: {entry["model"]: [entry] for entry in entries}
            for purpose, entries in source.items()
        }
        layer = {
            "id": layer_id, "tier": tier, "origin": origin, "values": values,
            "installation_cell": context["installation_cell"],
            "source": {"root": str(root), "path": str(path.relative_to(root))},
        }
        if tier in ("repository", "repository_machine"):
            layer["repository_id"] = context["repository_id"]
        if tier in ("machine", "repository_machine"):
            layer["execution_machine"] = context["execution_machine"]
        layers.append(layer)

    relative = Path(".github") / "copilot" / "model-routing.json"
    append("repository", "repository", "base", repository, repo / relative, repo)
    if context["binding"] == "bound":
        knowledge = Path(context["knowledge_root"])
        status, source = load(
            knowledge / relative, kind="knowledge", allowed=trusted(knowledge, home),
            diagnostics=diagnostics,
        )
        statuses["knowledge"] = status
        if operator_status != "invalid":
            append("knowledge", "repository", "knowledge", source, knowledge / relative, knowledge)
    append(
        "operator", "machine", "local_machine", operator,
        home / ".copilot" / "model-routing.json", home,
    )
    local = Path(owner["repository_machine_root"])
    status, source = load(
        local / relative, kind="repository-machine", allowed=True, diagnostics=diagnostics,
    )
    statuses["repository-machine"] = status
    if operator_status != "invalid":
        append(
            "repository-machine", "repository_machine", "repository_machine",
            source, local / relative, local,
        )
    result = compose(context, layers)
    values = {
        purpose: [entry[0] for entry in entries.values()]
        for purpose, entries in result["values"].items()
    }
    return values, statuses, result["provenance"]
