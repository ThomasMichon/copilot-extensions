"""Namespace resolver for GitHub Codespaces.

Implements the agent-bridge ``NamespaceResolver`` interface so that
codespace agents can be addressed as ``codespace:<name>`` without
pre-registration. The resolver queries ``gh codespace list`` on demand
and builds SpawnTargets that launch ``agent-codespaces ssh --stdio``.

Usage:
    from agent_codespaces.resolver import CodespaceResolver

    resolver = CodespaceResolver()
    bridge_resolver.register_namespace_resolver(resolver)

    # Then: agent-bridge send codespace:my-cs-name "do the work"
"""

from __future__ import annotations

import asyncio
import logging
import shutil
import sys
from typing import TYPE_CHECKING

from .config import load_merged_config
from .lifecycle import list_codespaces

if TYPE_CHECKING:
    from agent_bridge.agent_registry import NamespaceAgentInfo
    from agent_bridge.transport import SpawnTarget

log = logging.getLogger("agent-codespaces")


def _find_agent_codespaces_cmd() -> str:
    """Find the agent-codespaces CLI command path."""
    which = shutil.which("agent-codespaces")
    if which:
        return which
    return sys.executable


def _build_spawn_command(codespace_name: str) -> list[str]:
    """Build the spawn command for a codespace agent."""
    cmd_path = _find_agent_codespaces_cmd()
    if cmd_path == sys.executable:
        return [
            cmd_path, "-m", "agent_codespaces",
            "ssh", codespace_name, "--stdio",
            "--remote-cmd", "copilot --acp --stdio",
        ]
    return [
        cmd_path,
        "ssh", codespace_name, "--stdio",
        "--remote-cmd", "copilot --acp --stdio",
    ]


class CodespaceResolver:
    """Namespace resolver for ``codespace:<name>`` agent routing.

    Resolves codespace names on demand by querying ``gh codespace list``
    and returning SpawnTargets that use ``agent-codespaces ssh --stdio``
    for transport.
    """

    @property
    def prefix(self) -> str:
        return "codespace"

    async def resolve(self, name: str) -> "SpawnTarget":
        """Resolve a codespace name to a SpawnTarget.

        Verifies the codespace exists and is Available before returning.
        """
        from agent_bridge.transport import SpawnTarget

        codespaces = await asyncio.to_thread(list_codespaces)
        cs = None
        for c in codespaces:
            if c.name == name:
                cs = c
                break

        if cs is None:
            raise KeyError(
                f"Codespace '{name}' not found. "
                f"Available: {[c.name for c in codespaces if c.state == 'Available']}"
            )

        if cs.state != "Available":
            raise ValueError(
                f"Codespace '{name}' is in state '{cs.state}' "
                "(must be Available to spawn an agent)"
            )

        spawn_cmd = _build_spawn_command(name)
        log.info("Resolved codespace:%s -> %s", name, " ".join(spawn_cmd))

        config = load_merged_config()
        return SpawnTarget(
            type="command",
            spawn_command=spawn_cmd,
            user=config.ssh_user,
        )

    async def list(self) -> list["NamespaceAgentInfo"]:
        """List all codespaces as namespace agent info."""
        from agent_bridge.agent_registry import NamespaceAgentInfo

        codespaces = await asyncio.to_thread(list_codespaces)
        agents = []
        for cs in codespaces:
            repo_short = cs.repository.split("/")[-1] if cs.repository else ""
            display = cs.display_name or cs.name
            if repo_short:
                display = f"{display} ({repo_short})"

            description = f"GitHub Codespace: {cs.repository}"
            if cs.branch:
                description += f"@{cs.branch}"

            state = cs.state.lower() if cs.state else "unknown"

            agents.append(NamespaceAgentInfo(
                name=cs.name,
                display_name=display,
                description=description,
                icon="codespace",
                state=state,
            ))

        return agents

    async def ensure_ready(self, name: str) -> None:
        """Verify codespace is reachable.

        Currently just checks state. Future: could start a shutdown
        codespace and wait for it to become Available.
        """
        codespaces = await asyncio.to_thread(list_codespaces)
        for cs in codespaces:
            if cs.name == name:
                if cs.state == "Available":
                    return
                raise RuntimeError(
                    f"Codespace '{name}' is '{cs.state}'. "
                    "Start it first: gh codespace start -c {name}"
                )
        raise RuntimeError(f"Codespace '{name}' not found")
