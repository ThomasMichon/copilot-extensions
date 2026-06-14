"""Agent registry -- parse agent configs and resolve to spawn targets.

Loads agent profiles from acp-agents.json (or similar), cross-references
with machine topology, and resolves named agents to SpawnTargets.

Also auto-discovers local agents from agent-worktrees projects.yaml so
that loopback (same-machine) communication works without explicit config.

Supports **namespace resolvers** for prefixed agent names (e.g.
``codespace:my-cs``, ``admin:task``). A ``NamespaceResolver`` is an
async plugin that handles on-demand agent resolution for a given
prefix -- the resolver is called at dispatch time, so agent state is
always fresh (no TTL, no registration).
"""

from __future__ import annotations

import json
import logging
import os
import time
from abc import ABC, abstractmethod
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
    provider: str | None = None  # provider name (e.g. "codespaces")
    spawn_command: list[str] | None = None  # raw command for provider agents


@dataclass
class AgentProvider:
    """A registered external agent provider (e.g. codespaces).

    Providers contribute dynamic agents that are merged into the resolver.
    Agents expire after ``ttl`` seconds from ``registered_at`` (monotonic).
    """

    name: str
    agents: dict[str, AgentConfig] = field(default_factory=dict)
    registered_at: float = 0.0  # time.monotonic()
    ttl: float = 300.0  # seconds before agents expire (0 = no expiry)


@dataclass
class NamespaceAgentInfo:
    """Lightweight agent info returned by namespace resolvers."""

    name: str
    display_name: str = ""
    description: str = ""
    icon: str | None = None
    state: str = "available"  # resolver-defined (e.g. "available", "shutdown")


class NamespaceResolver(ABC):
    """Pluggable resolver for a namespace of agents.

    Namespace resolvers handle prefixed agent names (e.g.
    ``codespace:my-cs``). When agent-bridge encounters a colon in an
    agent name, it looks up the prefix in the namespace registry and
    delegates resolution to the matching resolver.

    Resolvers are async because they may need to query external systems
    (e.g. ``gh codespace list``, SSH health checks) at dispatch time.
    """

    @property
    @abstractmethod
    def prefix(self) -> str:
        """The namespace prefix this resolver handles (e.g. ``codespace``)."""
        ...

    @abstractmethod
    async def resolve(self, name: str) -> SpawnTarget:
        """Resolve a bare name (without prefix) to a SpawnTarget.

        Called at dispatch time when a session targets ``prefix:name``.
        The resolver should verify the target is reachable and return
        a SpawnTarget ready for ``transport.spawn()``.

        Raises:
            KeyError: Agent not found.
            ValueError: Agent exists but is not in a spawnable state.
            RuntimeError: Transient failure (SSH unreachable, etc.).
        """
        ...

    @abstractmethod
    async def list(self) -> list[NamespaceAgentInfo]:
        """Enumerate available agents in this namespace.

        Called by ``agent-bridge agents`` to show all reachable targets.
        May be slow (e.g. ``gh codespace list``); callers should cache
        or run concurrently.
        """
        ...

    async def ensure_ready(self, name: str) -> None:
        """Pre-flight check: ensure the target is ready for a session.

        Optional hook called before ``resolve()``. Implementations may
        start a shutdown codespace, wait for SSH, run health checks, etc.
        The default implementation is a no-op.

        Raises:
            RuntimeError: Target cannot be made ready.
        """


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
        data = json.loads(p.read_text(encoding="utf-8")) or {}
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

    Agent exposure defaults ON; a project adopted as reference-only carries
    ``expose_agent: false`` in projects.yaml and is skipped here.

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
    skipped: list[str] = []
    for project_name, project_data in projects.items():
        if not isinstance(project_data, dict):
            continue
        # Agent exposure defaults ON: an adopted project normally backs a local
        # loopback agent. A project explicitly adopted as reference-only
        # (agent-worktrees `register --no-agent` -> `expose_agent: false`) is
        # managed for worktrees but exposes no agent. Absent key => on.
        if not project_data.get("expose_agent", True):
            skipped.append(project_name)
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

    if skipped:
        log.debug(
            "Skipped %d reference-only project(s) with expose_agent=false: %s",
            len(skipped), skipped,
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


def _find_covering_agent(
    local_agent: AgentConfig,
    registry: dict[str, AgentConfig],
    machines: dict[str, MachineConfig],
) -> str | None:
    """Return the name of a registry agent that covers a local agent, or None.

    A registry agent "covers" an auto-discovered local agent when it
    targets the same project on the local machine in the local environment.
    """
    if not local_agent.project:
        return None

    machine, platform = _detect_local_machine(machines)
    if not machine:
        return None

    # Map platform to the ssh_environment name used in agent configs
    env_name = platform  # 'windows', 'wsl', 'linux'

    for name, agent in registry.items():
        if agent.auto_discovered or agent.project != local_agent.project:
            continue
        # Agent must target this machine (host matches machine key)
        if not agent.host:
            continue
        # Resolve the host to a machine key (could be alias or key)
        host_lower = agent.host.lower()
        target_machine = machines.get(host_lower)
        if not target_machine:
            # Try matching against machine keys case-insensitively
            for mk, mc in machines.items():
                if mk.lower() == host_lower:
                    target_machine = mc
                    break
        if target_machine and target_machine.key == machine.key:
            # Same machine -- check if the environment matches
            agent_env = (agent.ssh_environment or "").lower()
            if agent_env == env_name:
                return name

    return None


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

    # Auto-discover local agents from adopted projects; explicit wins.
    # Also suppress auto-discovered agents when a registry agent already
    # covers this machine+environment for the same project.
    discovered = discover_local_agents()
    if discovered and all_machines:
        _enrich_local_agents(discovered, all_machines)
    for name, agent in discovered.items():
        if name in all_agents:
            log.debug(
                "Skipping auto-discovered agent '%s' -- explicit entry exists", name,
            )
            continue
        # Check if a registry agent already covers this project on the
        # local machine+environment (making the auto-discovered one redundant).
        covering = _find_covering_agent(agent, all_agents, all_machines)
        if covering:
            log.info(
                "Suppressing auto-discovered agent '%s' -- registry agent "
                "'%s' covers this project on the local machine",
                name, covering,
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
        _register_namespace_resolvers(resolver)
        return resolver

    log.info("No topology profiles or local agents found")
    return None


def _register_namespace_resolvers(resolver: AgentResolver) -> None:
    """Auto-discover and register namespace resolvers from optional packages.

    Each resolver is imported from its package and registered on the
    AgentResolver. Import failures are logged at debug level and silently
    skipped -- namespace resolvers are optional extensions.
    """
    # codespace: -- GitHub Codespaces (agent-codespaces package)
    try:
        from agent_codespaces.resolver import CodespaceResolver

        resolver.register_namespace_resolver(CodespaceResolver())
        log.info("Registered codespace: namespace resolver (agent-codespaces)")
    except ImportError:
        log.debug("agent-codespaces not installed -- codespace: namespace unavailable")
    except Exception:
        log.warning(
            "Failed to register codespace: namespace resolver",
            exc_info=True,
        )

    # container: -- local Docker dev containers (agent-containers package)
    try:
        from agent_containers.resolver import ContainerResolver

        resolver.register_namespace_resolver(ContainerResolver())
        log.info("Registered container: namespace resolver (agent-containers)")
    except ImportError:
        log.debug("agent-containers not installed -- container: namespace unavailable")
    except Exception:
        log.warning(
            "Failed to register container: namespace resolver",
            exc_info=True,
        )

    # admin: -- elevated execution (built-in)
    try:
        from .admin_resolver import AdminResolver

        resolver.register_namespace_resolver(AdminResolver(resolver))
        log.info("Registered admin: namespace resolver")
    except Exception:
        log.warning(
            "Failed to register admin: namespace resolver",
            exc_info=True,
        )


class AgentResolver:
    """Resolves agent names to SpawnTargets using topology + registry.

    Cross-references the agent registry (which agents exist and how to
    configure them) with the machine topology (which machines exist and
    how to reach them via SSH).

    Supports **namespace resolvers** for prefixed agent names
    (``prefix:name``). Register resolvers via
    :meth:`register_namespace_resolver`.
    """

    def __init__(
        self,
        agents: dict[str, AgentConfig],
        machines: dict[str, MachineConfig],
    ) -> None:
        self._agents = agents
        self._machines = machines
        self._providers: dict[str, AgentProvider] = {}
        self._namespace_resolvers: dict[str, NamespaceResolver] = {}
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

        # Cache local identity for loopback detection
        self._local_machine, self._local_platform = _detect_local_machine(
            machines,
        )

    @property
    def agents(self) -> dict[str, AgentConfig]:
        return self._agents

    @property
    def machines(self) -> dict[str, MachineConfig]:
        return self._machines

    # --- Provider management ---

    def register_provider(
        self,
        name: str,
        agents: dict[str, AgentConfig],
        ttl: float = 300.0,
    ) -> AgentProvider:
        """Register or refresh an agent provider.

        Provider agents are merged into the resolver with lowest
        precedence -- static and auto-discovered agents always win
        on name conflicts.
        """
        provider = AgentProvider(
            name=name,
            agents=agents,
            registered_at=time.monotonic(),
            ttl=ttl,
        )
        self._providers[name] = provider
        log.info(
            "Registered provider '%s' with %d agents (ttl=%.0fs)",
            name, len(agents), ttl,
        )
        return provider

    def unregister_provider(self, name: str) -> bool:
        """Remove a provider. Returns True if it existed."""
        removed = self._providers.pop(name, None)
        if removed:
            log.info(
                "Unregistered provider '%s' (%d agents removed)",
                name, len(removed.agents),
            )
        return removed is not None

    def _is_provider_expired(self, provider: AgentProvider) -> bool:
        """Check if a provider's TTL has elapsed (monotonic clock)."""
        if provider.ttl <= 0:
            return False
        return (time.monotonic() - provider.registered_at) > provider.ttl

    def _live_provider_agents(self) -> dict[str, AgentConfig]:
        """Collect all non-expired provider agents.

        Expired providers are purged lazily. Static/auto-discovered
        agents override provider agents on name conflict.
        """
        expired = [
            name for name, p in self._providers.items()
            if self._is_provider_expired(p)
        ]
        for name in expired:
            log.info("Provider '%s' expired (ttl elapsed), removing", name)
            del self._providers[name]

        result: dict[str, AgentConfig] = {}
        for provider in self._providers.values():
            for agent_name, agent in provider.agents.items():
                if agent_name in self._agents:
                    continue  # static/auto-discovered wins
                if agent_name in result:
                    continue  # first provider wins
                result[agent_name] = agent
        return result

    def list_providers(self) -> list[dict[str, Any]]:
        """List registered providers with status metadata."""
        result = []
        for provider in self._providers.values():
            expired = self._is_provider_expired(provider)
            conflicts = [
                name for name in provider.agents
                if name in self._agents
            ]
            result.append({
                "name": provider.name,
                "agents": len(provider.agents),
                "active_agents": len(provider.agents) - len(conflicts),
                "conflicts": conflicts,
                "ttl": provider.ttl,
                "age": time.monotonic() - provider.registered_at,
                "expired": expired,
            })
        return result

    # --- Namespace resolver management ---

    def register_namespace_resolver(self, resolver: NamespaceResolver) -> None:
        """Register a namespace resolver for prefixed agent names.

        Example: a resolver with ``prefix="codespace"`` handles all
        agent names matching ``codespace:<name>``.

        Raises ValueError if a resolver for the same prefix is already
        registered.
        """
        prefix = resolver.prefix
        if prefix in self._namespace_resolvers:
            raise ValueError(
                f"Namespace resolver for '{prefix}:' already registered"
            )
        self._namespace_resolvers[prefix] = resolver
        log.info("Registered namespace resolver: %s:", prefix)

    def unregister_namespace_resolver(self, prefix: str) -> bool:
        """Remove a namespace resolver. Returns True if it existed."""
        removed = self._namespace_resolvers.pop(prefix, None)
        if removed:
            log.info("Unregistered namespace resolver: %s:", prefix)
        return removed is not None

    @property
    def namespace_resolvers(self) -> dict[str, NamespaceResolver]:
        """Read-only view of registered namespace resolvers."""
        return dict(self._namespace_resolvers)

    def _parse_namespaced_agent(
        self, agent_name: str,
    ) -> tuple[str, str] | None:
        """Split ``prefix:name`` into ``(prefix, name)``.

        Returns None if the name contains no colon or the prefix has no
        registered resolver.
        """
        if ":" not in agent_name:
            return None
        prefix, _, name = agent_name.partition(":")
        if prefix in self._namespace_resolvers and name:
            return prefix, name
        return None

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
        """Resolve an agent name to a SpawnTarget (sync path).

        Handles static, auto-discovered, and provider agents. For
        namespaced agents (``prefix:name``), use :meth:`resolve_async`.

        Raises:
            KeyError: Agent not found in registry.
            ValueError: Agent is managed (non-spawnable), target machine
                not found, or no suitable SSH environment available, or
                agent name is namespaced (requires async resolution).
        """
        # Check for namespace prefix -- require async path
        ns = self._parse_namespaced_agent(agent_name)
        if ns:
            raise ValueError(
                f"Agent '{agent_name}' uses namespace '{ns[0]}:' -- "
                "use resolve_async() for namespaced agents"
            )

        return self._resolve_static(agent_name)

    async def resolve_async(self, agent_name: str) -> SpawnTarget:
        """Resolve an agent name to a SpawnTarget (async path).

        Supports both regular agents and namespaced agents
        (``prefix:name``). For namespaced agents, calls
        ``ensure_ready()`` then ``resolve()`` on the namespace resolver.

        Raises:
            KeyError: Agent not found.
            ValueError: Agent not spawnable.
            RuntimeError: Namespace resolver failed.
        """
        ns = self._parse_namespaced_agent(agent_name)
        if ns:
            prefix, name = ns
            resolver = self._namespace_resolvers[prefix]
            log.info(
                "Resolving namespaced agent %s:%s via %s resolver",
                prefix, name, prefix,
            )
            await resolver.ensure_ready(name)
            return await resolver.resolve(name)

        return self._resolve_static(agent_name)

    def _resolve_static(self, agent_name: str) -> SpawnTarget:
        """Resolve via static/auto-discovered/provider registries."""
        config = self._agents.get(agent_name)
        if not config:
            # Check provider agents
            provider_agents = self._live_provider_agents()
            config = provider_agents.get(agent_name)
        if not config:
            raise KeyError(f"Agent '{agent_name}' not found in registry")

        if config.managed:
            raise ValueError(
                f"Agent '{agent_name}' is managed (non-spawnable) -- "
                "it cannot be started via agent-bridge transport"
            )

        # Provider agents with spawn_command bypass topology resolution
        if config.spawn_command:
            return SpawnTarget(
                type="command",
                spawn_command=config.spawn_command,
                env=config.env,
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

        # Loopback detection: if the resolved machine is the local machine
        # and the SSH environment matches our platform, spawn locally instead
        # of SSH-ing to ourselves. SSH loopback causes binstub stdout
        # pollution that breaks ACP JSON-RPC parsing.
        if (
            self._local_machine
            and machine.key == self._local_machine.key
            and ssh_env.name == self._local_platform
        ):
            log.info(
                "Loopback detected for agent '%s' (machine '%s', env '%s') "
                "-- spawning locally instead of SSH",
                agent_name, machine.key, ssh_env.name,
            )
            return SpawnTarget(
                type="local",
                cwd=config.cwd,
                copilot_path=config.copilot_path,
                copilot_args=config.copilot_args,
                env=config.env,
                project=config.project,
            )

        # Serialize auth hooks for the SpawnTarget (must be JSON-safe dicts)
        auth_hook_dicts = [
            {
                "name": h.name,
                "local_port": h.local_port,
                "remote_port": h.remote_port,
                "env": h.env,
            }
            for h in machine.auth_hooks
        ]

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
            auth_hooks=auth_hook_dicts,
        )

    def _agent_to_dict(self, config: AgentConfig) -> dict[str, Any]:
        """Convert an AgentConfig to API-ready dict."""
        spawnable = not config.managed
        if config.spawn_command:
            target_type = "command"
        elif config.host:
            target_type = "ssh"
        else:
            target_type = "local"
        return {
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
            "provider": config.provider,
        }

    def list_agents(self) -> list[dict[str, Any]]:
        """List all agents with metadata for the API.

        Includes static, auto-discovered, and live provider agents.
        Namespace agents are NOT included here (they require async
        enumeration). Use :meth:`list_agents_async` for the full list.
        """
        result = []
        for config in self._agents.values():
            result.append(self._agent_to_dict(config))

        # Add non-conflicting provider agents
        for config in self._live_provider_agents().values():
            result.append(self._agent_to_dict(config))

        return result

    async def list_agents_async(self) -> list[dict[str, Any]]:
        """List all agents including namespace-resolved agents.

        Calls ``list()`` on each registered namespace resolver to
        include dynamically discovered agents (e.g. live codespaces).
        """
        result = self.list_agents()

        for prefix, resolver in self._namespace_resolvers.items():
            try:
                ns_agents = await resolver.list()
                for agent in ns_agents:
                    result.append({
                        "name": f"{prefix}:{agent.name}",
                        "display_name": agent.display_name or agent.name,
                        "description": agent.description,
                        "icon": agent.icon,
                        "managed": False,
                        "spawnable": True,
                        "target_type": "command",
                        "host": "",
                        "ssh_user": None,
                        "ssh_environment": None,
                        "cwd": None,
                        "copilot_path": None,
                        "copilot_args": [],
                        "worktree_root": None,
                        "env": {},
                        "project": None,
                        "auto_discovered": False,
                        "provider": prefix,
                        "state": agent.state,
                    })
            except Exception:
                log.warning(
                    "Namespace resolver '%s' failed to list agents",
                    prefix, exc_info=True,
                )

        return result
