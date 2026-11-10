"""Select the Machines peer without activating it or imposing a domain capability."""

from __future__ import annotations

import os
from pathlib import Path

from agent_procutil.owned_budget import OwnedExecutionBudget


def selected(*, budget: OwnedExecutionBudget | None = None) -> tuple[list[str], dict[str, str], Path]:
    from . import _peer_launch, peer_launch_adapter, reconcile

    if "COPILOT_EXTENSIONS_CONTEXT" in os.environ:
        own = peer_launch_adapter.validate_context()
        if own is None:
            raise ValueError("Machines observation requires a valid installation context")
        prefix, python = _peer_launch.pinned_launch_prefix(
            "agent-worktrees", Path(own["pluginRoot"]), own["installReceipt"], "agent-machines",
        )
        environment = dict(os.environ)
    else:
        payload = reconcile.core_installed_payload_dir("agent-machines")
        if payload is None:
            raise ValueError("Machines payload is unavailable; use Installation & Updates")
        installation = reconcile.resolve_runtime_installation(
            "agent-machines", payload, **({"budget": budget} if budget is not None else {}),
        )
        if installation.actual_mode != "legacy" or installation.status != "ready" or installation.context is not None:
            raise ValueError("Machines observation requires the selected installation's native context")
        root = installation.runtime_root
        python = _peer_launch._peer_python_inprocess({
            "pluginId": "agent-machines", "pluginRoot": str(root),
            "versionsRoot": str(root / "versions"), "payloadRoot": str(payload),
        })
        if python is None:
            raise ValueError("Machines has no observable completed runtime")
        prefix = [str(python), "-I", "-B", "-X", "utf8", "-m", "agent_machines"]
        environment = _peer_launch.legacy_peer_environment(dict(os.environ))
    return prefix, environment, python
