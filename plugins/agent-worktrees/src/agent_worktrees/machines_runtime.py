"""Select the Machines peer without activating it or imposing a domain capability."""

from __future__ import annotations

import os
from pathlib import Path

from agent_procutil.owned_budget import OwnedExecutionBudget


def _payload_root(project_root: Path | None) -> Path | None:
    from . import reconcile

    if project_root is None:
        return reconcile.core_installed_payload_dir("agent-machines")
    from dataclasses import replace
    from dropin_registry import ScanAuthority
    from plugin_activation import current_declarations, declared_payload_roots
    from plugin_resolve import MarketplaceSourceKind, RepoPluginSettings, marketplace_source_kind

    project_root = Path(project_root)
    if not project_root.is_absolute():
        raise ValueError("Machines source observation requires an absolute project root")
    home = reconcile._home()
    declared = current_declarations(home=home, project_root=project_root)
    if declared.authority is ScanAuthority.INDETERMINATE:
        raise ValueError("Machines source declarations are unavailable or invalid")
    if reconcile.MARKETPLACE not in declared.settings.marketplaces:
        return reconcile.core_installed_payload_dir("agent-machines")
    kind = marketplace_source_kind(reconcile.MARKETPLACE, declared.settings)
    if kind is MarketplaceSourceKind.REMOTE:
        return reconcile.core_installed_payload_dir("agent-machines")
    if kind is not MarketplaceSourceKind.LOCAL:
        raise ValueError("Machines marketplace source selection is invalid")
    source = f"agent-machines@{reconcile.MARKETPLACE}"
    # Query the installed peer's source without enabling its Copilot plugin.
    scoped = replace(declared, settings=RepoPluginSettings(
        enabled={source: True}, marketplaces=declared.settings.marketplaces,
    ))
    return declared_payload_roots(scoped, home=home)[source]


def selected(
    *, project_root: Path | None = None, budget: OwnedExecutionBudget | None = None,
) -> tuple[list[str], dict[str, str], Path]:
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
        payload = _payload_root(project_root)
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
