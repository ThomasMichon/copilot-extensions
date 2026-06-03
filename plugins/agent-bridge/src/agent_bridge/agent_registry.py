"""Agent registry -- parse agent configs and resolve to spawn targets.

Loads agent profiles from acp-agents.json (or similar), cross-references
with machine topology, and resolves named agents to SpawnTargets.

Also auto-discovers local agents from agent-worktrees projects.yaml so
that loopback (same-machine) communication works without explicit config.
"""

from __future__ import annotations

import json
import logging
import os
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .topology import MachineConfig, SshEnvironment
from .transport import SpawnTarget

log = logging.getLogger("agent-bridge")

_PROJECTS_YAML_DEFAULT = "~/.agent-worktrees/projects.yaml"


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
    icon: str | None = None
    worktree_root: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    project: str | None = None  # agent-worktrees project (binstub name)
    setup_script: str | None = None
    auto_discovered: bool = False  # True for agents from projects.yaml


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
            icon=config.get("icon"),
            worktree_root=config.get("worktree_root"),
            env={str(k): str(v) for k, v in config.get("env", {}).items()},
            project=config.get("project"),
            setup_script=config.get("setup_script"),
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


def discover_local_agents() -> dict[str, AgentConfig]:
    """Auto-discover local agents from agent-worktrees projects.yaml.

    For each adopted project, synthesizes a local AgentConfig that uses
    the project binstub for spawning. This enables loopback communication
    (same-machine, cross-worktree) without explicit acp-agents.json entries.

    Returns an empty dict if projects.yaml is missing or unparseable.
    """
    try:
        import yaml
    except ImportError:
        log.debug("pyyaml not available -- skipping local agent discovery")
        return {}

    projects_path = Path(
        os.environ.get("AGENT_WORKTREES_PROJECTS_YAML", _PROJECTS_YAML_DEFAULT)
    ).expanduser()

    if not projects_path.exists():
        log.debug("projects.yaml not found at %s -- no local agents", projects_path)
        return {}

    try:
        data = yaml.safe_load(projects_path.read_text()) or {}
    except Exception as exc:
        log.warning("Failed to parse projects.yaml at %s: %s", projects_path, exc)
        return {}

    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        log.warning("projects.yaml 'projects' key is not a dict -- skipping")
        return {}

    discovered: dict[str, AgentConfig] = {}
    for project_name, project_data in projects.items():
        if not isinstance(project_data, dict):
            continue
        anchor = project_data.get("anchor", "")
        discovered[project_name] = AgentConfig(
            name=project_name,
            project=project_name,
            cwd=anchor or None,
            display_name=f"{project_name} (local)",
            description=f"Local agent for {project_name} (auto-discovered from projects.yaml)",
            auto_discovered=True,
        )

    if discovered:
        log.info(
            "Auto-discovered %d local agent(s) from projects.yaml: %s",
            len(discovered), list(discovered.keys()),
        )
    return discovered


def _detect_platform() -> str:
    """Detect the local platform: 'windows', 'wsl', or 'linux'."""
    import sys
    if sys.platform == "win32":
        return "windows"
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


def _detect_local_machine(
    machines: dict[str, MachineConfig],
) -> tuple[MachineConfig | None, str]:
    """Match the local hostname to a machine in topology.

    Returns (machine, platform) where platform is 'windows', 'wsl', or 'linux'.
    """
    import socket
    hostname = socket.gethostname().lower()
    platform = _detect_platform()

    # Try exact key match first, then check aliases
    machine = machines.get(hostname)
    if machine:
        return machine, platform

    # Try case-insensitive key match
    for key, mc in machines.items():
        if key.lower() == hostname:
            return mc, platform

    return None, platform


def _enrich_local_agents(
    agents: dict[str, AgentConfig],
    machines: dict[str, MachineConfig],
) -> None:
    """Set display_name and description on auto-discovered agents using machine identity."""
    machine, platform = _detect_local_machine(machines)
    if not machine:
        return

    suffix = " (WSL)" if platform == "wsl" else ""
    display_name = f"{machine.display_name}{suffix}"

    for agent in agents.values():
        agent.display_name = display_name
        agent.description = (
            f"Local agent on {display_name} "
            f"(auto-discovered from projects.yaml)"
        )

    log.info(
        "Enriched %d local agent(s) with machine identity: %s",
        len(agents), display_name,
    )


def build_resolver(cfg) -> AgentResolver | None:  # noqa: ANN001
    """Build an AgentResolver from config profiles + local discovery.

    Loads topology profiles from config, then merges auto-discovered
    local agents. Explicit registry entries always take precedence over
    auto-discovered ones.

    Args:
        cfg: Loaded BridgeConfig with topologies dict.

    Returns:
        AgentResolver if any agents or machines were found, else None.
    """
    from .topology import load_machines_yaml

    all_machines: dict[str, MachineConfig] = {}
    all_agents: dict[str, AgentConfig] = {}

    for _profile_name, profile in cfg.topologies.items():
        if profile.machines_yaml:
            machines = load_machines_yaml(profile.machines_yaml)
            all_machines.update(machines)
        if profile.agents_config:
            agents_cfg = load_agent_registry(profile.agents_config)
            all_agents.update(agents_cfg)

    # Auto-discover local agents from adopted projects; explicit wins
    discovered = discover_local_agents()
    if discovered and all_machines:
        _enrich_local_agents(discovered, all_machines)
    for name, agent in discovered.items():
        if name in all_agents:
            log.debug(
                "Skipping auto-discovered agent '%s' -- explicit entry exists", name,
            )
        else:
            all_agents[name] = agent

    if all_machines or all_agents:
        resolver = AgentResolver(all_agents, all_machines)
        log.info(
            "Resolver built: %d machines, %d agents (%d auto-discovered)",
            len(all_machines), len(all_agents),
            sum(1 for a in all_agents.values() if a.auto_discovered),
        )
        return resolver

    log.info("No topology profiles or local agents found")
    return None


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
        # Build alias -> (machine, env) index for fast lookup
        self._alias_index: dict[str, tuple[MachineConfig, SshEnvironment]] = {}
        for machine in machines.values():
            for env in machine.ssh_environments:
                if env.alias in self._alias_index:
                    log.warning(
                        "Duplicate SSH alias '%s' (machines '%s' and '%s')",
                        env.alias,
                        self._alias_index[env.alias][0].key,
                        machine.key,
                    )
                else:
                    self._alias_index[env.alias] = (machine, env)

    @property
    def agents(self) -> dict[str, AgentConfig]:
        return self._agents

    @property
    def machines(self) -> dict[str, MachineConfig]:
        return self._machines

    def _resolve_machine(
        self, host: str, ssh_environment: str | None = None,
    ) -> tuple[MachineConfig, SshEnvironment | None]:
        """Resolve a host to a machine, checking keys then SSH aliases.

        Returns (machine, forced_env) where forced_env is set when the
        host matched via an SSH alias (the caller should use that
        environment directly rather than selecting one).

        Raises:
            ValueError: Host not found by key or alias, or conflicting
                ssh_environment specified alongside an alias match.
        """
        # Direct machine key match
        machine = self._machines.get(host)
        if machine:
            return machine, None

        # Alias-based fallback
        entry = self._alias_index.get(host)
        if entry:
            machine, matched_env = entry
            if ssh_environment and ssh_environment != matched_env.name:
                raise ValueError(
                    f"Host '{host}' resolved via SSH alias to machine "
                    f"'{machine.key}' environment '{matched_env.name}', "
                    f"but agent config specifies ssh_environment="
                    f"'{ssh_environment}' (conflict)"
                )
            return machine, matched_env

        raise ValueError(
            f"Machine '{host}' not found by key or SSH alias in topology"
        )

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

        # SSH agent -- resolve machine (by key or alias) and environment
        machine, alias_env = self._resolve_machine(
            config.host, config.ssh_environment,
        )

        if not machine.ssh_ready:
            raise ValueError(
                f"Machine '{machine.key}' is not marked as SSH-ready "
                "in the topology"
            )

        # When resolved via alias, use the matched environment directly.
        # Otherwise, select environment via the standard logic.
        if alias_env:
            ssh_env = alias_env
            # Still enforce shell compatibility for non-binstub targets
            if not config.project:
                POSIX_SHELLS = {"bash", "sh", "zsh", "dash", "fish"}
                if ssh_env.shell not in POSIX_SHELLS:
                    raise ValueError(
                        f"Agent '{agent_name}' resolved via SSH alias "
                        f"'{config.host}' to environment '{ssh_env.name}' "
                        f"(shell={ssh_env.shell}), but non-binstub SSH "
                        "targets require a POSIX-compatible shell"
                    )
        elif config.project:
            ssh_env = machine.get_ssh_env(config.ssh_environment)
        else:
            ssh_env = machine.get_spawnable_ssh_env(config.ssh_environment)

        if not ssh_env:
            available = [e.name for e in machine.ssh_environments]
            if config.project:
                raise ValueError(
                    f"No SSH environment "
                    f"{repr(config.ssh_environment) + ' ' if config.ssh_environment else ''}"
                    f"for agent '{agent_name}' on '{machine.key}'. "
                    f"Available: {available}"
                )
            posix = [
                e.name for e in machine.ssh_environments
                if e.shell in {"bash", "sh", "zsh", "dash", "fish"}
            ]
            raise ValueError(
                f"No suitable SSH environment for agent '{agent_name}' on "
                f"'{machine.key}'. Available: {available}, "
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
            ssh_shell=ssh_env.shell,
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
                "icon": config.icon,
                "managed": config.managed,
                "spawnable": spawnable,
                "target_type": target_type,
                "host": config.host or "",
                "ssh_user": config.ssh_user,
                "ssh_environment": config.ssh_environment,
                "cwd": config.cwd,
                "copilot_path": config.copilot_path,
                "copilot_args": config.copilot_args,
                "worktree_root": config.worktree_root,
                "env": config.env or {},
                "project": config.project,
                "auto_discovered": config.auto_discovered,
            })
        return result
