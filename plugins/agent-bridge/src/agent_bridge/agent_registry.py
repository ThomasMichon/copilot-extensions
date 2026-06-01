"""Agent registry -- parse agent configs and resolve to spawn targets.

Loads agent profiles from acp-agents.json (or similar), cross-references
with machine topology, and resolves named agents to SpawnTargets.
"""

from __future__ import annotations

import json
import logging
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .topology import MachineConfig, SshEnvironment
from .transport import SpawnTarget

log = logging.getLogger("agent-bridge")


@dataclass
class AgentConfig:
    """Parsed agent configuration from acp-agents.json."""

    name: str
    host: str | None = None
    ssh_user: str | None = None
    ssh_environment: str | None = None
    cwd: str | None = None
    copilot_path: str | None = None
    copilot_args: list[str] = field(default_factory=list)
    managed: bool = False
    description: str | None = None
    display_name: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    project: str | None = None  # agent-worktrees project (binstub name)


def parse_agent_registry(data: dict[str, Any]) -> dict[str, AgentConfig]:
    """Parse raw acp-agents.json data into typed AgentConfig objects."""
    registry: dict[str, AgentConfig] = {}
    for name, config in data.items():
        registry[name] = AgentConfig(
            name=name,
            host=config.get("host"),
            ssh_user=config.get("ssh_user"),
            ssh_environment=config.get("ssh_environment"),
            cwd=config.get("cwd"),
            copilot_path=config.get("copilot_path"),
            copilot_args=config.get("copilot_args", []),
            managed=bool(config.get("managed")),
            description=config.get("description"),
            display_name=config.get("display_name"),
            env={str(k): str(v) for k, v in config.get("env", {}).items()},
            project=config.get("project"),
        )
    return registry


def load_agent_registry(path: str | Path) -> dict[str, AgentConfig]:
    """Load and parse an agent registry file (acp-agents.json)."""
    p = Path(path).expanduser()
    if not p.exists():
        log.warning("Agent registry not found at %s", p)
        return {}
    try:
        data = json.loads(p.read_text()) or {}
        registry = parse_agent_registry(data)
        log.info("Loaded %d agents from %s", len(registry), p)
        return registry
    except Exception as exc:
        log.error("Failed to parse agent registry at %s: %s", p, exc)
        return {}


class AgentResolver:
    """Resolves agent names to SpawnTargets using topology + registry.

    Cross-references the agent registry (which agents exist and how to
    configure them) with the machine topology (which machines exist and
    how to reach them via SSH).
    """

    def __init__(
        self,
        agents: dict[str, AgentConfig],
        machines: dict[str, MachineConfig],
    ) -> None:
        self._agents = agents
        self._machines = machines

    @property
    def agents(self) -> dict[str, AgentConfig]:
        return self._agents

    @property
    def machines(self) -> dict[str, MachineConfig]:
        return self._machines

    def resolve(self, agent_name: str) -> SpawnTarget:
        """Resolve an agent name to a SpawnTarget.

        Raises:
            KeyError: Agent not found in registry.
            ValueError: Agent is managed (non-spawnable), target machine
                not found, or no suitable SSH environment available.
        """
        config = self._agents.get(agent_name)
        if not config:
            raise KeyError(f"Agent '{agent_name}' not found in registry")

        if config.managed:
            raise ValueError(
                f"Agent '{agent_name}' is managed (non-spawnable) -- "
                "it cannot be started via agent-bridge transport"
            )

        if not config.host:
            # Local agent
            return SpawnTarget(
                type="local",
                cwd=config.cwd,
                copilot_path=config.copilot_path,
                copilot_args=config.copilot_args,
                env=config.env,
                project=config.project,
            )

        # SSH agent -- resolve machine and environment
        machine = self._machines.get(config.host)
        if not machine:
            raise ValueError(
                f"Agent '{agent_name}' targets machine '{config.host}' "
                "which is not in the topology"
            )

        if not machine.ssh_ready:
            raise ValueError(
                f"Machine '{config.host}' is not marked as SSH-ready "
                "in the topology"
            )

        # When a project binstub is configured, the remote command does
        # not require POSIX shell constructs (no cd/export/exec) -- it
        # just invokes the binstub directly.  Any SSH environment works.
        # Without a binstub, the command uses POSIX constructs, so we
        # restrict to POSIX-compatible shells.
        if config.project:
            ssh_env = machine.get_ssh_env(config.ssh_environment)
        else:
            ssh_env = machine.get_spawnable_ssh_env(config.ssh_environment)
        if not ssh_env:
            available = [e.name for e in machine.ssh_environments]
            if config.project:
                raise ValueError(
                    f"No SSH environment "
                    f"{repr(config.ssh_environment) + ' ' if config.ssh_environment else ''}"
                    f"for agent '{agent_name}' on '{config.host}'. "
                    f"Available: {available}"
                )
            posix = [
                e.name for e in machine.ssh_environments
                if e.shell in {"bash", "sh", "zsh", "dash", "fish"}
            ]
            raise ValueError(
                f"No suitable SSH environment for agent '{agent_name}' on "
                f"'{config.host}'. Available: {available}, "
                f"POSIX-compatible: {posix}. "
                "Non-binstub SSH targets require a POSIX-compatible shell."
            )

        return SpawnTarget(
            type="ssh",
            cwd=config.cwd,
            host=ssh_env.alias,
            user=ssh_env.user or config.ssh_user,
            copilot_path=config.copilot_path,
            copilot_args=config.copilot_args,
            env=config.env,
            project=config.project,
        )

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents with metadata for the API."""
        result = []
        for config in self._agents.values():
            spawnable = not config.managed
            target_type = "local" if not config.host else "ssh"
            result.append({
                "name": config.name,
                "display_name": config.display_name or config.name,
                "description": config.description or "",
                "managed": config.managed,
                "spawnable": spawnable,
                "target_type": target_type,
                "host": config.host or "",
            })
        return result
