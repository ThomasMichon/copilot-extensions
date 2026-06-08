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
        """List all static agents as potential admin targets.

        Only local and command agents are included (SSH agents
        cannot be elevated from the bridge side).
        """
        from .agent_registry import NamespaceAgentInfo

        agents = []
        for config in self._parent.agents.values():
            if config.managed:
                continue
            # Skip SSH agents (can't be locally elevated)
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
        """Verify the inner agent exists and elevation tools are available."""
        # Verify agent exists
        if name not in self._parent.agents:
            from .agent_registry import AgentProvider
            # Check provider agents too
            provider_agents = self._parent._live_provider_agents()
            if name not in provider_agents:
                raise RuntimeError(
                    f"Agent '{name}' not found for admin elevation"
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
