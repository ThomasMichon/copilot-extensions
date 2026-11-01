"""Model-routing's native entry replacement over the owner-delivered data plan."""

from __future__ import annotations

import os
import sys
from pathlib import Path


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
    context = context_from_owner(report)
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
    local = Path(report["repository_machine_root"])
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
