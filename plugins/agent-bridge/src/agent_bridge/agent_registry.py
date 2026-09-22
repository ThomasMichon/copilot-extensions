"""Agent registry -- parse agent configs and resolve to spawn targets."""

from __future__ import annotations

import logging
import os
import shutil
import sys
from pathlib import Path

from .agent_registry_common import (
    AgentConfig,
    AgentRegistryLoadError,
    AmbiguousAgentError,
    NamespaceAgentInfo,
    _NAMESPACE_LIST_DEFAULT_TTL,
    _NAMESPACE_LIST_OFF,
    _NAMESPACE_LIST_RESOLVER_DEFAULT_TIMEOUT,
    _NAMESPACE_LIST_RESOLVER_TIMEOUT_ENV,
    _NAMESPACE_LIST_TTL_ENV,
    _PROJECTS_YAML_DEFAULT,
    _REPOS_YAML_DEFAULT,
    _normalize_repo_basename,
)
from .agent_registry_namespace import (
    CliNamespaceResolver,
    NamespaceResolver,
    RestrictedCliNamespaceResolver,
    _NS_BAD_STATE_EXIT,
    _NS_NOT_FOUND_EXIT,
)
from .agent_registry_relay import (
    FileTokenAuthorizer,
    FileTokenValidator,
    _apply_relay_profile,
    _register_provider_relay,
    _relay_profile_via_cli,
    _relay_source_by_name,
    register_credential_sources,
)
from .agent_registry_resolver import AgentResolver
from .agent_registry_topology import (
    _effective_spawn_defaults,
    _enrich_local_agents,
    _find_covering_agent,
    _load_related_entries,
    _match_machine_shortname,
    _short_machine_agent_name,
    _split_repo_venue,
    derive_topology_agents,
    discover_local_agents,
    infer_control_plane_project,
    load_agent_registry,
    load_elevated_projects,
    load_local_repos,
    parse_agent_registry,
)
from .topology import MachineConfig, SshEnvironment
from .transport import PluginRef, SpawnTarget

log = logging.getLogger("agent-bridge")

_parse_agent_registry_impl = parse_agent_registry
_load_agent_registry_impl = load_agent_registry
_discover_local_agents_impl = discover_local_agents
_load_elevated_projects_impl = load_elevated_projects
_enrich_local_agents_impl = _enrich_local_agents
_find_covering_agent_impl = _find_covering_agent
_short_machine_agent_name_impl = _short_machine_agent_name
_match_machine_shortname_impl = _match_machine_shortname
_split_repo_venue_impl = _split_repo_venue
_load_related_entries_impl = _load_related_entries
_load_local_repos_impl = load_local_repos
_infer_control_plane_project_impl = infer_control_plane_project
_derive_topology_agents_impl = derive_topology_agents
_effective_spawn_defaults_impl = _effective_spawn_defaults


def parse_agent_registry(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _parse_agent_registry_impl(*args, **kwargs)


def load_agent_registry(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _load_agent_registry_impl(*args, **kwargs)


def discover_local_agents(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _discover_local_agents_impl(*args, **kwargs)


def load_elevated_projects(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _load_elevated_projects_impl(*args, **kwargs)


def _enrich_local_agents(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _enrich_local_agents_impl(*args, **kwargs)


def _find_covering_agent(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _find_covering_agent_impl(*args, **kwargs)


def _short_machine_agent_name(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _short_machine_agent_name_impl(*args, **kwargs)


def _match_machine_shortname(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _match_machine_shortname_impl(*args, **kwargs)


def _split_repo_venue(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _split_repo_venue_impl(*args, **kwargs)


def _load_related_entries(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _load_related_entries_impl(*args, **kwargs)


def load_local_repos(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _load_local_repos_impl(*args, **kwargs)


def infer_control_plane_project(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _infer_control_plane_project_impl(*args, **kwargs)


def derive_topology_agents(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _derive_topology_agents_impl(*args, **kwargs)


def _effective_spawn_defaults(*args, **kwargs):
    """Compatibility wrapper for the historical root import path."""
    return _effective_spawn_defaults_impl(*args, **kwargs)


def _namespace_list_resolver_timeout() -> float:
    """Resolve the per-resolver ``list()`` timeout from the environment."""
    raw = str(os.environ.get(_NAMESPACE_LIST_RESOLVER_TIMEOUT_ENV, "")).strip().lower()
    if raw in _NAMESPACE_LIST_OFF:
        return 0.0
    if not raw:
        return _NAMESPACE_LIST_RESOLVER_DEFAULT_TIMEOUT
    try:
        value = float(raw)
        return value if value > 0 else 0.0
    except ValueError:
        return _NAMESPACE_LIST_RESOLVER_DEFAULT_TIMEOUT


def _namespace_list_ttl() -> float:
    """Resolve the namespace-list cache TTL from the environment."""
    raw = str(os.environ.get(_NAMESPACE_LIST_TTL_ENV, "")).strip().lower()
    if raw in _NAMESPACE_LIST_OFF:
        return 0.0
    if not raw:
        return _NAMESPACE_LIST_DEFAULT_TTL
    try:
        value = float(raw)
        return value if value > 0 else 0.0
    except ValueError:
        return _NAMESPACE_LIST_DEFAULT_TTL


def _detect_platform() -> str:
    """Detect the local platform: 'windows', 'wsl', or 'linux'."""
    if sys.platform == "win32":
        return "windows"
    try:
        with open("/proc/version") as handle:
            if "microsoft" in handle.read().lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


def _detect_local_machine(
    machines: dict[str, MachineConfig],
) -> tuple[MachineConfig | None, str]:
    """Match the local hostname to a machine in topology."""
    import socket

    hostname = socket.gethostname().lower()
    platform = _detect_platform()

    machine = machines.get(hostname)
    if machine:
        return machine, platform
    for key, machine_config in machines.items():
        if key.lower() == hostname:
            return machine_config, platform
    for machine_config in machines.values():
        if getattr(machine_config, "hostname", "") and machine_config.hostname.lower() == hostname:
            return machine_config, platform
    return None, platform


def resolve_repo_remote(repo: str) -> str | None:
    """Resolve a logical repo name to its git remote URL."""
    try:
        import yaml
    except ImportError:
        log.debug("pyyaml not available -- cannot resolve repo remote")
        return None

    repos_path = Path(
        os.environ.get("AGENT_WORKTREES_REPOS_YAML", _REPOS_YAML_DEFAULT)
    ).expanduser()
    if not repos_path.exists():
        log.debug("repos.yaml not found at %s -- no repo remote", repos_path)
        return None

    try:
        data = yaml.safe_load(repos_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("Failed to parse repos.yaml at %s: %s", repos_path, exc)
        return None

    repos = data.get("repos")
    if not isinstance(repos, dict):
        return None

    entry = repos.get(repo)
    if not isinstance(entry, dict):
        want = _normalize_repo_basename(repo)
        for key, value in repos.items():
            if not isinstance(value, dict):
                continue
            if _normalize_repo_basename(str(key)) == want:
                entry = value
                break
    if not isinstance(entry, dict):
        log.debug("repo '%s' not in repos registry %s", repo, repos_path)
        return None

    remote = entry.get("remote")
    return str(remote) if isinstance(remote, str) and remote.strip() else None


def _agent_worktrees_bin() -> str | None:
    """Resolve the local ``agent-worktrees`` binstub, or None."""
    exe = shutil.which("agent-worktrees")
    if exe:
        return exe
    base = Path.home() / ".local" / "bin"
    candidates = ("agent-worktrees.cmd", "agent-worktrees") if os.name == "nt" else ("agent-worktrees",)
    for candidate in candidates:
        path = base / candidate
        if path.exists():
            return str(path)
    return None


def build_resolver(cfg) -> AgentResolver | None:  # noqa: ANN001
    """Build an AgentResolver from config profiles + local discovery."""
    from .topology import (
        TopologyLoadError,
        load_control_plane_project,
        load_machines_yaml,
    )

    all_machines: dict[str, MachineConfig] = {}
    all_agents: dict[str, AgentConfig] = {}
    topology_errors: list[str] = []

    for profile_name, profile in cfg.topologies.items():
        if not profile.machines_yaml:
            if profile.agents_config:
                agents_path = Path(profile.agents_config).expanduser()
                try:
                    all_agents.update(load_agent_registry(agents_path, strict=True))
                except AgentRegistryLoadError as exc:
                    topology_errors.append(f"{profile_name}: {exc}")
            else:
                topology_errors.append(
                    f"{profile_name}: no machines_yaml or agents_config configured"
                )
            continue
        try:
            machines = load_machines_yaml(profile.machines_yaml, strict=True)
        except TopologyLoadError as exc:
            topology_errors.append(f"{profile_name}: {exc}")
            continue
        all_machines.update(machines)
        if profile.agents_config:
            agents_path = Path(profile.agents_config).expanduser()
            try:
                all_agents.update(load_agent_registry(agents_path, strict=True))
            except AgentRegistryLoadError as exc:
                topology_errors.append(f"{profile_name}: {exc}")
        local_repos = load_local_repos()
        cp_project = load_control_plane_project(profile.machines_yaml)
        cp_source = "machines.yaml"
        if not cp_project:
            cp_project = infer_control_plane_project(local_repos, profile.machines_yaml)
            cp_source = "repos.yaml (agent flag)"
        if cp_project:
            log.info("Control-plane project '%s' (from %s)", cp_project, cp_source)
        repo_root = Path(profile.machines_yaml).expanduser().resolve().parent
        related = _load_related_entries(repo_root)
        local_machine, local_platform = _detect_local_machine(machines)
        from .config import load_repo_bridge_config

        repo_cfg = load_repo_bridge_config(repo_root)
        eff_copilot_args, eff_env = _effective_spawn_defaults(profile, repo_cfg)
        derived = derive_topology_agents(
            machines,
            cp_project,
            related,
            local_machine,
            local_platform,
            local_repos,
            load_elevated_projects(),
            default_copilot_args=eff_copilot_args,
            default_env=eff_env,
        )
        for name, agent in derived.items():
            all_agents.setdefault(name, agent)

    discovered = discover_local_agents()
    if discovered and all_machines:
        _enrich_local_agents(discovered, all_machines)
    for name, agent in discovered.items():
        if name in all_agents:
            log.debug(
                "Skipping auto-discovered agent '%s' -- explicit entry exists",
                name,
            )
            continue
        covering = _find_covering_agent(agent, all_agents, all_machines)
        if covering:
            log.info(
                "Suppressing auto-discovered agent '%s' -- registry agent "
                "'%s' covers this project on the local machine",
                name,
                covering,
            )
        else:
            all_agents[name] = agent

    if all_machines or all_agents or topology_errors:
        resolver = AgentResolver(
            all_agents,
            all_machines,
            topology_errors=topology_errors,
        )
        log.info(
            "Resolver built: %d machines, %d agents (%d derived, %d auto-discovered)",
            len(all_machines),
            len(all_agents),
            sum(1 for agent in all_agents.values() if agent.derived),
            sum(1 for agent in all_agents.values() if agent.auto_discovered),
        )
        _register_namespace_resolvers(resolver)
        return resolver

    log.info("No topology profiles or local agents found")
    return None


def daemon_resolver(cfg) -> AgentResolver:  # noqa: ANN001
    """Resolver for the long-running daemon -- ALWAYS returns one."""
    resolver = build_resolver(cfg)
    if resolver is None:
        resolver = AgentResolver({}, {})
        _register_namespace_resolvers(resolver)
    return resolver


def _register_namespace_resolvers(resolver: AgentResolver) -> None:
    """Register namespace resolvers: declarative providers + built-in ``admin:``."""
    resolver.refresh_provider_resolvers(force=True)
    try:
        from .admin_resolver import AdminResolver

        resolver.register_namespace_resolver(AdminResolver(resolver))
        log.info("Registered admin: namespace resolver")
    except Exception:
        log.warning(
            "Failed to register admin: namespace resolver",
            exc_info=True,
        )


__all__ = [
    "AgentConfig",
    "AgentRegistryLoadError",
    "AgentResolver",
    "AmbiguousAgentError",
    "CliNamespaceResolver",
    "FileTokenAuthorizer",
    "FileTokenValidator",
    "NamespaceAgentInfo",
    "NamespaceResolver",
    "PluginRef",
    "RestrictedCliNamespaceResolver",
    "SpawnTarget",
    "SshEnvironment",
    "_NS_BAD_STATE_EXIT",
    "_NS_NOT_FOUND_EXIT",
    "_NAMESPACE_LIST_DEFAULT_TTL",
    "_NAMESPACE_LIST_OFF",
    "_NAMESPACE_LIST_RESOLVER_DEFAULT_TIMEOUT",
    "_NAMESPACE_LIST_RESOLVER_TIMEOUT_ENV",
    "_NAMESPACE_LIST_TTL_ENV",
    "_PROJECTS_YAML_DEFAULT",
    "_REPOS_YAML_DEFAULT",
    "_agent_worktrees_bin",
    "_apply_relay_profile",
    "_detect_local_machine",
    "_detect_platform",
    "_effective_spawn_defaults",
    "_enrich_local_agents",
    "_find_covering_agent",
    "_load_related_entries",
    "_match_machine_shortname",
    "_namespace_list_resolver_timeout",
    "_namespace_list_ttl",
    "_normalize_repo_basename",
    "_register_provider_relay",
    "_register_namespace_resolvers",
    "_relay_profile_via_cli",
    "_relay_source_by_name",
    "_short_machine_agent_name",
    "_split_repo_venue",
    "build_resolver",
    "daemon_resolver",
    "derive_topology_agents",
    "discover_local_agents",
    "infer_control_plane_project",
    "load_agent_registry",
    "load_elevated_projects",
    "load_local_repos",
    "MachineConfig",
    "parse_agent_registry",
    "register_credential_sources",
    "resolve_repo_remote",
]
