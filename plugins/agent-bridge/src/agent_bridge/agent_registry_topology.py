"""Agent-registry parsing, discovery, and topology-derived roster helpers."""

from __future__ import annotations

import json
import logging
import os
import subprocess
from dataclasses import replace
from pathlib import Path
from typing import Any

from agent_procutil import no_window_flags

from .agent_registry_common import (
    AgentConfig,
    AgentRegistryLoadError,
    _PROJECTS_YAML_DEFAULT,
)
from .topology import MachineConfig, SshEnvironment

log = logging.getLogger("agent-bridge")


def parse_agent_registry(data: dict[str, Any]) -> dict[str, AgentConfig]:
    """Parse raw acp-agents.json data into typed AgentConfig objects."""
    registry: dict[str, AgentConfig] = {}
    for name, config in data.items():
        raw_aliases = config.get("aliases", [])
        if (
            not isinstance(raw_aliases, list)
            or any(not isinstance(alias, str) for alias in raw_aliases)
        ):
            raise ValueError(f"agent {name!r} aliases must be a list of strings")
        raw_mcp_servers = config.get("mcp_servers", [])
        if (
            not isinstance(raw_mcp_servers, list)
            or any(not isinstance(spec, dict) for spec in raw_mcp_servers)
        ):
            raise ValueError(f"agent {name!r} mcp_servers must be a list of objects")
        raw_worktree_discovery = bool(config.get("worktree_discovery", True))
        raw_spawnable_as_target = config.get("spawnable_as_target")
        if raw_spawnable_as_target is None:
            spawnable_as_target = not (
                bool(config.get("project")) and not raw_worktree_discovery
            )
        else:
            spawnable_as_target = bool(raw_spawnable_as_target)
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
            aliases=list(raw_aliases),
            icon=config.get("icon"),
            worktree_root=config.get("worktree_root"),
            env={str(k): str(v) for k, v in config.get("env", {}).items()},
            project=config.get("project"),
            spawnable_as_target=spawnable_as_target,
            worktree_discovery=raw_worktree_discovery,
            setup_script=config.get("setup_script"),
            requires_admin=bool(config.get("requires_admin")),
            mcp_servers=[dict(spec) for spec in raw_mcp_servers],
        )
    return registry


def load_agent_registry(
    path: str | Path, *, strict: bool = False,
) -> dict[str, AgentConfig]:
    """Load and parse an agent registry file (acp-agents.json)."""
    from . import agent_registry as compat

    registry_path = Path(path).expanduser()
    if not registry_path.exists():
        message = f"agent registry not found at {registry_path}"
        if strict:
            raise AgentRegistryLoadError(message)
        log.warning(message)
        return {}
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8")) or {}
        registry = compat.parse_agent_registry(data)
        log.info("Loaded %d agents from %s", len(registry), registry_path)
        return registry
    except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
        message = f"failed to parse agent registry at {registry_path}: {exc}"
        if strict:
            raise AgentRegistryLoadError(message) from exc
        log.error(message)
        return {}


def discover_local_agents() -> dict[str, AgentConfig]:
    """Auto-discover local agents from agent-worktrees projects.yaml."""
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
        if not project_data.get("expose_agent", True):
            skipped.append(project_name)
            continue
        anchor = project_data.get("anchor", "")
        discovered[project_name] = AgentConfig(
            name=project_name,
            project=project_name,
            cwd=anchor or None,
            display_name=f"{project_name} (local)",
            description=(
                f"Local agent for {project_name} "
                "(auto-discovered from projects.yaml)"
            ),
            auto_discovered=True,
            requires_admin=bool(
                project_data.get("requires_admin") or project_data.get("elevated")
            ),
        )

    if skipped:
        log.debug(
            "Skipped %d reference-only project(s) with expose_agent=false: %s",
            len(skipped),
            skipped,
        )
    if discovered:
        log.info(
            "Auto-discovered %d local agent(s) from projects.yaml: %s",
            len(discovered),
            list(discovered.keys()),
        )
    return discovered


def load_elevated_projects() -> set[str]:
    """Return the set of adopted project names that require elevation."""
    try:
        import yaml
    except ImportError:
        return set()

    projects_path = Path(
        os.environ.get("AGENT_WORKTREES_PROJECTS_YAML", _PROJECTS_YAML_DEFAULT)
    ).expanduser()
    if not projects_path.exists():
        return set()
    try:
        data = yaml.safe_load(projects_path.read_text()) or {}
    except Exception as exc:
        log.warning("Failed to parse projects.yaml at %s: %s", projects_path, exc)
        return set()

    projects = data.get("projects", {})
    if not isinstance(projects, dict):
        return set()
    elevated: set[str] = set()
    for project_name, project_data in projects.items():
        if not isinstance(project_data, dict):
            continue
        if project_data.get("requires_admin") or project_data.get("elevated"):
            elevated.add(str(project_name))
    return elevated


def _enrich_local_agents(
    agents: dict[str, AgentConfig],
    machines: dict[str, MachineConfig],
) -> None:
    """Set display_name and description on auto-discovered agents."""
    from . import agent_registry as compat

    machine, platform = compat._detect_local_machine(machines)
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
        len(agents),
        display_name,
    )


def _find_covering_agent(
    local_agent: AgentConfig,
    registry: dict[str, AgentConfig],
    machines: dict[str, MachineConfig],
) -> str | None:
    """Return the name of a registry agent that covers a local agent, or None."""
    if not local_agent.project:
        return None

    from . import agent_registry as compat

    machine, platform = compat._detect_local_machine(machines)
    if not machine:
        return None

    env_name = platform
    for name, agent in registry.items():
        if agent.auto_discovered or agent.derived or agent.project != local_agent.project:
            continue
        if not agent.host:
            continue
        host_lower = agent.host.lower()
        target_machine = machines.get(host_lower)
        if not target_machine:
            for machine_key, machine_config in machines.items():
                if machine_key.lower() == host_lower:
                    target_machine = machine_config
                    break
        if target_machine and target_machine.key == machine.key:
            agent_env = (agent.ssh_environment or "").lower()
            if agent_env == env_name:
                return name
    return None


def _short_machine_agent_name(machine: MachineConfig, env: SshEnvironment) -> str:
    """Friendly agent name for a control-plane (machine, env) pair."""
    base = (machine.display_name or machine.key).strip()
    name = (env.name or "").lower()
    if name in ("", "windows", "win", "linux"):
        return base
    if name == "wsl":
        return f"{base}-wsl"
    return f"{base}-{name}"


def _match_machine_shortname(
    machines: dict[str, MachineConfig], short: str,
) -> MachineConfig | None:
    """Resolve a related.yaml short name to a MachineConfig."""
    short_lower = short.strip().lower()
    if not short_lower:
        return None
    for machine in machines.values():
        if (machine.display_name or "").strip().lower() == short_lower:
            return machine
        key_lower = machine.key.lower()
        if (
            key_lower == short_lower
            or key_lower.rsplit("-", 1)[-1] == short_lower
            or key_lower.endswith("-" + short_lower)
        ):
            return machine
    return None


def _split_repo_venue(agent_name: str) -> tuple[str | None, str]:
    """Split a ``<repo>@<venue>`` address into ``(repo, venue)``."""
    if "@" in agent_name:
        repo, _, venue = agent_name.partition("@")
        repo, venue = repo.strip(), venue.strip()
        if repo and venue:
            return repo, venue
    return None, agent_name


def _load_related_entries(repo_root: Path) -> list[tuple[str, list[str], str]]:
    """Parse ``<repo>/.agent-worktrees/related.yaml`` minimally."""
    related_path = repo_root / ".agent-worktrees" / "related.yaml"
    if not related_path.exists():
        return []
    try:
        import yaml

        data = yaml.safe_load(related_path.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("Failed to parse related.yaml at %s: %s", related_path, exc)
        return []
    out: list[tuple[str, list[str], str]] = []
    related = data.get("related") or {}
    if not isinstance(related, dict):
        return out
    for name, entry in related.items():
        if not isinstance(entry, dict):
            continue
        locus = entry.get("locus") or {}
        raw_machines = locus.get("machines") if isinstance(locus, dict) else None
        machines = (
            [str(machine).strip() for machine in raw_machines if str(machine).strip()]
            if isinstance(raw_machines, list)
            else []
        )
        delegate = entry.get("delegate")
        if isinstance(delegate, dict):
            delegate = delegate.get("via", "")
        out.append((str(name), machines, str(delegate or "").strip().lower()))
    return out


def load_local_repos() -> list[dict]:
    """Live-query the local per-machine repo registry (normalized)."""
    from . import agent_registry as compat

    exe = compat._agent_worktrees_bin()
    if not exe:
        log.debug("agent-worktrees binstub not found -- no local repo registry")
        return []

    child_env = {
        key: value
        for key, value in os.environ.items()
        if key not in ("VIRTUAL_ENV", "PYTHONHOME", "__PYVENV_LAUNCHER__", "PYTHONPATH")
    }
    try:
        proc = subprocess.run(
            [exe, "repos", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=no_window_flags(),
            env=child_env,
        )
    except Exception as exc:
        log.warning("agent-worktrees repos list failed: %s", exc)
        return []
    if proc.returncode != 0:
        log.debug("agent-worktrees repos list exited %s", proc.returncode)
        return []
    try:
        doc = json.loads(proc.stdout or "{}")
    except (ValueError, TypeError):
        log.debug("agent-worktrees repos list emitted non-JSON")
        return []
    return list(doc.get("repos", [])) if isinstance(doc, dict) else []


def infer_control_plane_project(
    repos: list[dict], machines_yaml_path: str | Path,
) -> str | None:
    """Infer the control-plane project from the live per-machine repo registry."""
    try:
        machines_path = Path(machines_yaml_path).expanduser().resolve()
    except Exception:
        return None

    def _resolve(raw: object) -> Path | None:
        if not raw:
            return None
        try:
            return Path(str(raw)).expanduser().resolve()
        except Exception:
            return None

    best_name: str | None = None
    best_len = -1
    for entry in repos:
        if not isinstance(entry, dict) or not entry.get("agent"):
            continue
        name = str(entry.get("name", "")).strip()
        if not name:
            continue
        paths = entry.get("paths") or {}
        if not isinstance(paths, dict):
            continue
        for raw in paths.values():
            checkout = _resolve(raw)
            if checkout is None:
                continue
            if machines_path == checkout or checkout in machines_path.parents:
                length = len(str(checkout))
                if length > best_len:
                    best_name, best_len = name, length
    return best_name


def derive_topology_agents(
    machines: dict[str, MachineConfig],
    control_plane_project: str | None,
    related: list[tuple[str, list[str], str]],
    local_machine: MachineConfig | None,
    local_platform: str = "",
    repos: list[dict] | None = None,
    elevated_projects: set[str] | None = None,
    *,
    default_copilot_args: list[str] | None = None,
    default_env: dict[str, str] | None = None,
) -> dict[str, AgentConfig]:
    """Synthesize the agent roster from topology (machines × repos × envs)."""
    from . import agent_registry as compat

    out: dict[str, AgentConfig] = {}

    def _machine_metadata(machine: MachineConfig) -> str:
        parts = [machine.description] if machine.description else []
        if machine.capabilities:
            parts.append(f"capabilities: {', '.join(machine.capabilities)}")
        return f" — {'; '.join(parts)}" if parts else ""

    def _is_loopback(machine: MachineConfig, env: SshEnvironment) -> bool:
        return bool(
            local_machine
            and machine.key == local_machine.key
            and env.name == local_platform
        )

    if control_plane_project:
        for machine in machines.values():
            for env in machine.ssh_environments:
                if not (_is_loopback(machine, env) or machine.ssh_ready):
                    continue
                name = compat._short_machine_agent_name(machine, env)
                if name in out:
                    name = f"{name}-{(env.name or '').lower()}"
                out[name] = AgentConfig(
                    name=name,
                    host=machine.key,
                    ssh_environment=env.name or None,
                    project=control_plane_project,
                    derived=True,
                    display_name=f"{machine.display_name} [{env.name}]",
                    aliases=[env.alias] if env.alias and env.alias != name else [],
                    description=(
                        f"Control-plane '{control_plane_project}' on "
                        f"{machine.display_name} ({env.name})"
                        f"{_machine_metadata(machine)} [derived from topology]"
                    ),
                )

    for repo, repo_machines, delegate in related:
        if delegate != "agent-bridge":
            continue
        for short in repo_machines:
            machine = compat._match_machine_shortname(machines, short)
            if not machine:
                continue
            if local_machine and machine.key == local_machine.key:
                continue
            if not machine.ssh_ready:
                continue
            env = machine.get_spawnable_ssh_env() or (
                machine.ssh_environments[0] if machine.ssh_environments else None
            )
            name = f"{repo}@{machine.display_name}"
            if name in out:
                continue
            stable_venue = env.alias if env and env.alias else machine.key
            stable_name = f"{repo}@{stable_venue}"
            out[name] = AgentConfig(
                name=name,
                host=machine.key,
                ssh_environment=(env.name if env else None),
                project=repo,
                derived=True,
                display_name=name,
                aliases=[stable_name] if stable_name != name else [],
                description=(
                    f"'{repo}' on {machine.display_name}"
                    f"{_machine_metadata(machine)} [derived from related.yaml]"
                ),
            )

    if local_machine and repos:
        elevated_projects = elevated_projects or set()
        env = local_machine.get_ssh_env(local_platform) if local_platform else None
        reachable = bool((env and _is_loopback(local_machine, env)) or local_machine.ssh_ready)
        if reachable:
            spawn_env = env or local_machine.get_spawnable_ssh_env()
            venue = local_machine.display_name or local_machine.key
            stable_venue = (
                spawn_env.alias if spawn_env and spawn_env.alias else local_machine.key
            )
            env_name = spawn_env.name if spawn_env else None
            for entry in repos:
                if not isinstance(entry, dict) or not entry.get("agent"):
                    continue
                repo = str(entry.get("name", "")).strip()
                if not repo:
                    continue
                name = f"{repo}@{venue}"
                if name in out:
                    continue
                out[name] = AgentConfig(
                    name=name,
                    host=local_machine.key,
                    ssh_environment=env_name,
                    project=repo,
                    derived=True,
                    display_name=name,
                    aliases=[f"{repo}@{stable_venue}"] if stable_venue != venue else [],
                    requires_admin=repo in elevated_projects,
                    description=(
                        f"'{repo}' on {local_machine.display_name}"
                        f"{_machine_metadata(local_machine)} "
                        "[derived from repos.yaml agent-backing checkout]"
                    ),
                )

    if default_copilot_args or default_env:
        for name, agent in list(out.items()):
            out[name] = replace(
                agent,
                copilot_args=list(default_copilot_args or agent.copilot_args),
                env={**(default_env or {}), **agent.env},
            )
    return out


def _effective_spawn_defaults(
    profile: Any, repo_cfg: Any,
) -> tuple[list[str], dict[str, str]]:
    """Resolve the spawn defaults for a profile: machine-local wins, else in-repo."""
    profile_set = getattr(profile, "model_fields_set", set())

    def _pick(field: str, repo_value: object) -> object:
        if field in profile_set:
            return getattr(profile, field)
        return repo_value if repo_cfg is not None else getattr(profile, field)

    copilot_args = _pick(
        "default_copilot_args",
        repo_cfg.default_copilot_args if repo_cfg else None,
    )
    env = _pick("default_env", repo_cfg.default_env if repo_cfg else None)
    return list(copilot_args), dict(env)
