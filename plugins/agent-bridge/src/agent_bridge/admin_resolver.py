"""Namespace resolver for elevated (admin) agent contexts.

Implements the ``NamespaceResolver`` interface so agents can be
addressed as ``admin:<name>`` to run in elevated contexts. The resolver
delegates name resolution to the parent ``AgentResolver``, then wraps
the spawn command in the platform-appropriate elevation mechanism.

Platform behavior:
    Windows:  Routes the session through the **elevated sub-daemon relay**
              (a second, elevated agent-bridge bound to a loopback port).
              The ``admin:`` prefix is purely a routing cue -- it selects the
              same relay transport a bare ``requires_admin`` agent uses, with
              no ``gsudo`` / ``Start-Process RunAs`` (those give the elevated
              child no stdio pipe, so the ACP handshake closes) and no
              CLI-side difference.
    Linux/WSL: Wraps the spawn command with ``sudo -A`` (requires
               SUDO_ASKPASS to be configured for non-interactive use).

Usage:
    from agent_bridge.admin_resolver import AdminResolver

    admin = AdminResolver(parent_resolver)
    parent_resolver.register_namespace_resolver(admin)

    # Then: agent-bridge send admin:lambda-core-wsl "install the thing"
"""

from __future__ import annotations

import asyncio
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

    def _elevate_target_posix(self, target: "SpawnTarget") -> "SpawnTarget":
        """Wrap a SpawnTarget with ``sudo -A`` for Linux/WSL elevation.

        Windows elevation does not go through here -- it routes through the
        elevated sub-daemon relay (see :meth:`resolve`).
        """
        from .transport import SpawnTarget as ST

        # Build the effective command that would be spawned, then sudo-wrap it.
        if target.spawn_command:
            base_cmd = list(target.spawn_command)
        elif target.copilot_path:
            base_cmd = [target.copilot_path, "--acp", "--stdio"]
            base_cmd.extend(target.copilot_args or [])
        else:
            copilot = shutil.which("copilot") or "copilot"
            base_cmd = [copilot, "--acp", "--stdio"]
            base_cmd.extend(target.copilot_args or [])

        return ST(
            type="command",
            spawn_command=_wrap_elevated_posix(base_cmd),
            cwd=target.cwd,
            env=target.env,
            project=target.project,
        )

    async def resolve(self, name: str) -> "SpawnTarget":
        """Resolve an agent name and route it for elevated execution.

        The inner name is resolved via the parent resolver (static path only
        -- nested namespace resolution is not supported). On Windows the
        session is relayed through the elevated sub-daemon (the ``admin:``
        prefix is just the cue to elevate); on Linux/WSL it is ``sudo``-wrapped.
        """
        from . import elevated
        from .transport import SpawnTarget as ST

        try:
            target = self._parent._resolve_static(name)
        except KeyError:
            raise KeyError(
                f"Admin agent '{name}' not found in registry "
                "(admin: resolves against static agents only)"
            )

        if target.type == "ssh":
            raise ValueError(
                "Cannot elevate SSH agents -- elevation must be "
                "configured on the remote side (e.g., via sudoers)"
            )

        # Windows: relay through the elevated sub-daemon -- identical transport
        # to a bare requires_admin agent, no gsudo / RunAs, no CLI-side diff.
        # The sub-daemon (itself elevated) resolves the bare name locally and
        # runs the elevated agent-worktrees / copilot flow in the target repo.
        if elevated.relay_applicable(True):
            loop = asyncio.get_running_loop()
            token = await loop.run_in_executor(None, elevated.ensure_running)
            cmd = elevated.relay_spawn_command(name, token=token)
            log.info(
                "Routing admin:%s via elevated sub-daemon relay (port %d)",
                name, elevated.ELEVATED_PORT,
            )
            return ST(
                type="command", spawn_command=cmd, project=target.project,
            )

        # Windows but already elevated (e.g. this *is* the sub-daemon): spawn
        # locally -- the child inherits elevation; relaying would recurse.
        if self._platform == "windows":
            log.info(
                "admin:%s -- daemon already elevated, spawning locally", name,
            )
            return target

        # Linux/WSL: no sub-daemon; elevate with sudo -A.
        log.info(
            "Elevating agent '%s' via sudo (platform=%s)", name, self._platform,
        )
        return self._elevate_target_posix(target)

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

        # Verify elevation prerequisites. On Windows elevation routes through
        # the elevated sub-daemon relay (started on demand by ensure_running),
        # so no gsudo is required. On Linux/WSL it uses sudo -A.
        if self._platform != "windows":
            import os
            if not os.environ.get("SUDO_ASKPASS"):
                log.warning(
                    "SUDO_ASKPASS not set -- admin: elevation may "
                    "prompt interactively (will fail from services)"
                )
