"""Read-only installed Vault selection for data-only repository setup."""

from __future__ import annotations

from dataclasses import dataclass, field
import json
import os
from pathlib import Path

from . import _peer_launch, peer_launch_adapter, reconcile
from .repository_setup import SetupError, _environment


@dataclass(frozen=True)
class VaultInvocation:
    argv: tuple[str, ...]
    binding: str
    environment: dict[str, str] = field(compare=False)

    def verify(self) -> None:
        if selected() != self:
            raise SetupError("Installed Vault selection changed; preserve and requalify the original setup.")


def selected() -> VaultInvocation:
    """Resolve existing ownership only; never install, activate or use PATH."""
    try:
        environment = _environment(attended=False)
        if "COPILOT_EXTENSIONS_CONTEXT" in os.environ:
            own = peer_launch_adapter.validate_context()
            if own is None:
                raise ValueError("An explicit installation context is required")
            root = Path(own["pluginRoot"])
            prefix, python = _peer_launch.pinned_launch_prefix(
                "agent-worktrees", root, own["installReceipt"], "agent-vault",
            )
            peer, _peer_environment, _python = _peer_launch._resolve_peer(
                "agent-worktrees", root, own["installReceipt"], "agent-vault",
                allow_native=False, pinned_python=python,
            )
            binding = json.dumps([own, peer], sort_keys=True)
        else:
            payload = reconcile.core_installed_payload_dir("agent-vault")
            if payload is None:
                raise ValueError("Installed Vault payload is unavailable")
            installation = reconcile.resolve_runtime_installation("agent-vault", payload)
            if (
                installation.actual_mode != "legacy" or installation.desired_mode != "legacy"
                or installation.status != "ready" or installation.context is not None
            ):
                raise ValueError("Vault requires its selected native installation context")
            root = installation.runtime_root
            python = _peer_launch._peer_python_inprocess({
                "pluginId": "agent-vault", "pluginRoot": str(root),
                "versionsRoot": str(root / "versions"), "payloadRoot": str(payload),
            })
            if python is None:
                raise ValueError("Vault has no completed observable runtime")
            identity = _peer_launch.retained_runtime_identity(root, python, "agent_vault")
            prefix = _peer_launch.retained_runtime_prefix(
                root, python, "agent_vault", runtime_identity=identity,
            )
            binding = json.dumps([str(payload), str(root), installation.actual_mode])
            environment = _peer_launch.legacy_peer_environment(environment)
            for key in ("KPDB", "VAULT_GROUP", "AGENT_VAULT", "_AGENT_WORKTREES_VAULT_RUNTIME_GUARD"):
                environment.pop(key, None)
            environment["AGENT_VAULT_HOME"] = str(root)
        environment["AGENT_VAULT_NO_SELFPROVISION"] = "1"
        environment.pop("AGENT_VAULT_PINNED_CONTEXT", None)
        environment.pop("_AGENT_WORKTREES_VAULT_RUNTIME_GUARD", None)
        return VaultInvocation(tuple(prefix), binding, environment)
    except (OSError, ValueError, RuntimeError, ImportError) as error:
        raise SetupError(
            "Installed Vault owner is unavailable or refused; use its normal installation owner, then resume."
        ) from error
