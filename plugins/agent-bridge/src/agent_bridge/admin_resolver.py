"""Namespace resolver for elevated (admin) agent contexts.

Implements the ``NamespaceResolver`` interface so agents can be
addressed as ``admin:<name>`` to run in elevated contexts. The resolver
delegates name resolution to the parent ``AgentResolver``, then wraps
the spawn command in the platform-appropriate elevation mechanism.

Platform behavior:
    Windows:  Wraps the spawn command with a scheduled-task technique
              (runs as the current user with highest privileges) since
              UAC cannot be used non-interactively from a service.
    Linux/WSL: Wraps the spawn command with ``sudo -A`` (requires
               SUDO_ASKPASS to be configured for non-interactive use).

Usage:
    from agent_bridge.admin_resolver import AdminResolver

    admin = AdminResolver(parent_resolver)
    parent_resolver.register_namespace_resolver(admin)

    # Then: agent-bridge send admin:lambda-core-wsl "install the thing"
"""

from __future__ import annotations

import logging
import shutil
import sys
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from .agent_registry import AgentResolver, NamespaceAgentInfo
    from .transport import SpawnTarget

log = logging.getLogger("agent-bridge")


def _detect_platform() -> str:
    """Detect the local platform: 'windows', 'wsl', or 'linux'."""
    if sys.platform == "win32":
        return "windows"
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


def _wrap_elevated_windows(spawn_command: list[str]) -> list[str]:
    """Wrap a command for elevated execution on Windows.

    Uses ``gsudo`` if available (preferred -- allows non-interactive
    elevation from services). Falls back to a PowerShell
    Start-Process -Verb RunAs wrapper, though that approach is
    interactive and may not work from headless services.
    """
    gsudo = shutil.which("gsudo")
    if gsudo:
        return [gsudo] + spawn_command

    # Fallback: powershell Start-Process with -Verb RunAs
    # This is interactive (UAC prompt) and won't work from services,
    # but it's better than nothing for interactive use.
    escaped_args = " ".join(f'"{a}"' for a in spawn_command[1:])
    return [
        "powershell", "-NoProfile", "-Command",
        f'Start-Process -FilePath "{spawn_command[0]}" '
        f'-ArgumentList {escaped_args} -Verb RunAs -Wait',
    ]


def _wrap_elevated_posix(spawn_command: list[str]) -> list[str]:
    """Wrap a command for elevated execution on Linux/WSL.

    Requires SUDO_ASKPASS to be set for non-interactive elevation.
    """
    return ["sudo", "-A"] + spawn_command


class AdminResolver:
    """Namespace resolver for ``admin:<name>`` elevated agent routing.

    Resolves the inner agent name via the parent resolver's static
    resolution, then wraps the resulting spawn command in platform-
    appropriate elevation.

    Only agents that resolve to ``type="local"`` or ``type="command"``
    can be elevated. SSH agents are already remote and elevation should
    be handled on the remote side.
    """

    def __init__(self, parent: "AgentResolver") -> None:
        self._parent = parent
        self._platform = _detect_platform()

    @property
    def prefix(self) -> str:
        return "admin"

    @property
    def bare_addressable(self) -> bool:
        # admin: is a modifier namespace -- it mirrors every static agent to
        # wrap it for elevation. It must stay opt-in (explicit ``admin:``
        # prefix) so a bare name never resolves to, or collides with, an
        # elevated twin. See AgentResolver._gather_bare_candidates.
        return False

    def _elevate_target(self, target: "SpawnTarget") -> "SpawnTarget":
        """Wrap a SpawnTarget's spawn mechanism for elevated execution."""
        from .transport import SpawnTarget as ST

        if target.type == "ssh":
            raise ValueError(
                "Cannot elevate SSH agents -- elevation must be "
                "configured on the remote side (e.g., via sudoers)"
            )

        # Build the effective command that would be spawned
        if target.spawn_command:
            base_cmd = list(target.spawn_command)
        elif target.copilot_path:
            base_cmd = [target.copilot_path, "--acp", "--stdio"]
            base_cmd.extend(target.copilot_args or [])
        else:
            # Default copilot path
            copilot = shutil.which("copilot") or "copilot"
            base_cmd = [copilot, "--acp", "--stdio"]
            base_cmd.extend(target.copilot_args or [])

        if self._platform == "windows":
            elevated_cmd = _wrap_elevated_windows(base_cmd)
        else:
            elevated_cmd = _wrap_elevated_posix(base_cmd)

        return ST(
            type="command",
            spawn_command=elevated_cmd,
            cwd=target.cwd,
            env=target.env,
            project=target.project,
        )

    async def resolve(self, name: str) -> "SpawnTarget":
        """Resolve an agent name and wrap for elevated execution.

        The inner name is resolved via the parent resolver (static
        path only -- nested namespace resolution is not supported).
        """
        try:
            target = self._parent._resolve_static(name)
        except KeyError:
            raise KeyError(
                f"Admin agent '{name}' not found in registry "
                "(admin: resolves against static agents only)"
            )

        log.info(
            "Elevating agent '%s' for admin execution (platform=%s)",
            name, self._platform,
        )
        return self._elevate_target(target)

    async def list(self) -> list["NamespaceAgentInfo"]:
        """List static agents that opted into admin elevation.

        Elevation is **opt-in**: only agents with ``requires_admin: true``
        (in acp-agents.json or projects.yaml) get an ``admin:<name>`` twin.
        This avoids blanket-mirroring every local agent -- which would make
        each one ambiguous with its own elevated twin. SSH agents cannot be
        elevated from the bridge side and are excluded regardless.
        """
        from .agent_registry import NamespaceAgentInfo

        agents = []
        for config in self._parent.agents.values():
            if config.managed:
                continue
            if not config.requires_admin:
                continue
            # Skip SSH agents (can't be elevated locally)
            if config.host and not config.spawn_command:
                continue

            agents.append(NamespaceAgentInfo(
                name=config.name,
                display_name=f"{config.display_name or config.name} (elevated)",
                description=f"Elevated: {config.description or config.name}",
                icon="shield",
                state="available",
            ))

        return agents

    async def ensure_ready(self, name: str) -> None:
        """Verify the inner agent exists, opted into admin, and tools exist."""
        # Verify agent exists and opted into elevation. Provider agents
        # (e.g. codespaces) are remote and never admin-eligible here.
        config = self._parent.agents.get(name)
        if config is None:
            if name in self._parent._live_provider_agents():
                raise RuntimeError(
                    f"Agent '{name}' is a provider agent and cannot be "
                    "elevated from the bridge side"
                )
            raise RuntimeError(
                f"Agent '{name}' not found for admin elevation"
            )
        if not config.requires_admin:
            raise RuntimeError(
                f"Agent '{name}' is not configured for admin elevation -- "
                "set 'requires_admin: true' for it (acp-agents.json or "
                "projects.yaml) to expose an admin: twin"
            )

        # Verify elevation tools
        if self._platform == "windows":
            if not shutil.which("gsudo"):
                log.warning(
                    "gsudo not found -- admin: elevation will use "
                    "interactive UAC fallback (may not work from services)"
                )
        else:
            import os
            if not os.environ.get("SUDO_ASKPASS"):
                log.warning(
                    "SUDO_ASKPASS not set -- admin: elevation may "
                    "prompt interactively (will fail from services)"
                )
