"""Agent registry -- parse agent configs and resolve to spawn targets.

Derives the agent roster from committed topology (machines.yaml + each repo's
related.yaml) and the local machine's live repo registry (``repos.yaml`` -- its
``agent: true`` checkouts) via :func:`derive_topology_agents` -- machines × repos ×
environments -- and cross-references machine topology to resolve named agents to
SpawnTargets. A hand-authored ``acp-agents.json`` is still honored if a profile
sets ``agents_config`` (deprecated, explicit-wins back-compat).

Also auto-discovers local agents from agent-worktrees projects.yaml so
that loopback (same-machine) communication works without explicit config.

Supports **namespace resolvers** for prefixed agent names (e.g.
``codespace:my-cs``, ``admin:task``). A ``NamespaceResolver`` is an
async plugin that handles on-demand agent resolution for a given
prefix -- the resolver is called at dispatch time, so agent state is
always fresh (no TTL, no registration).
"""

from __future__ import annotations

import asyncio
import inspect
import json
import logging
import os
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from .topology import MachineConfig, SshEnvironment
from .transport import PluginRef, SpawnTarget

log = logging.getLogger("agent-bridge")

_PROJECTS_YAML_DEFAULT = "~/.agent-worktrees/projects.yaml"
_REPOS_YAML_DEFAULT = "~/.agent-worktrees/repos.yaml"


def _repo_basename_key(repo: object) -> str:
    """Normalize a repo key for basename fallback matching."""
    return str(repo).strip().lower().split("/")[-1].replace(".", "-").replace("_", "-")


def resolve_repo_remote(repo: str) -> str | None:
    """Resolve a logical repo name to its git remote URL.

    Reads the agent-worktrees global repos registry
    (``~/.agent-worktrees/repos.yaml``, override via ``AGENT_WORKTREES_REPOS_YAML``)
    and returns ``repos.<repo>.remote``. Used to thread a ``repo_remote`` into a
    ``<repo>@<venue>`` dispatch so a venue that hosts by convention (a CodeSpace's
    ``/workspaces/<basename>`` layout, #174) can clone the repo if it is missing.

    Matching is exact on the registry key first, then a case-insensitive fallback
    on the key's basename (so ``example-web`` matches an ``example-web`` entry regardless
    of case). Returns ``None`` when the registry is absent/unparseable or the repo
    (or its ``remote``) is unknown -- the caller decides whether that is fatal
    (for a pre-populated venue folder it is not; for a clone-if-missing it is).
    """
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
        want = _repo_basename_key(repo)
        for key, val in repos.items():
            if not isinstance(val, dict):
                continue
            if _repo_basename_key(key) == want:
                entry = val
                break
    if not isinstance(entry, dict):
        log.debug("repo '%s' not in repos registry %s", repo, repos_path)
        return None

    remote = entry.get("remote")
    return str(remote) if isinstance(remote, str) and remote.strip() else None


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
    derived: bool = False  # True for agents synthesized from topology (machines × repos)
    requires_admin: bool = False  # opt-in: expose an admin:<name> elevated twin
    provider: str | None = None  # provider name (e.g. "codespaces")
    spawn_command: list[str] | None = None  # raw command for provider agents
    codespace: dict | None = None  # structured CS metadata (#177) for the
    #                                CodeSpaceSpawner path (name/repo/acp_command/
    #                                workspace_folder); avoids parsing spawn_command


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
    # Alternate names this agent also answers to (e.g. a codespace's friendly
    # display name in addition to its raw GUID name). Used for bare-name and
    # prefixed resolution so a caller need not know the raw name (#50).
    aliases: list[str] = field(default_factory=list)


class AmbiguousAgentError(Exception):
    """A bare agent name matched more than one agent across namespaces.

    Carries the fully-qualified candidates (``namespace:name`` plus a bare
    label for non-namespaced/static agents) so the message can enumerate every
    colliding target and tell the caller how to disambiguate.
    """

    def __init__(self, name: str, candidates: list[str]) -> None:
        self.name = name
        self.candidates = candidates
        listed = ", ".join(candidates)
        super().__init__(
            f"Agent name '{name}' is ambiguous -- it matches "
            f"{len(candidates)} agents: {listed}. "
            "Qualify it with a namespace (e.g. 'codespace:<name>') or use the "
            "exact name to disambiguate."
        )


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
    async def resolve(
        self, name: str, *, extra_plugins: "list[PluginRef]" = (),
        repo: str | None = None, repo_remote: str | None = None,
    ) -> SpawnTarget:
        """Resolve a bare name (without prefix) to a SpawnTarget.

        Called at dispatch time when a session targets ``prefix:name``.
        The resolver should verify the target is reachable and return
        a SpawnTarget ready for ``transport.spawn()``.

        ``repo`` (optional) is the caller-requested workspace repo for a
        ``<repo>@<venue>`` address -- the venue should launch that repo's
        checkout instead of its default, or raise if it cannot host it. A
        resolver that does not accept ``repo`` signals (to agent-bridge) that
        cross-repo dispatch to its venues is unsupported.

        ``repo_remote`` (optional) is that repo's git remote URL (resolved
        host-side from the repos registry). A venue that hosts repos by
        convention (a CodeSpace's ``/workspaces/<basename>`` layout) uses it to
        clone-if-missing. Resolvers that do not accept it simply do not receive
        it (agent-bridge only passes kwargs the resolver's signature declares).

        ``extra_plugins`` (optional) is a set of **related-repo** plugins that
        agent-bridge has decided to inject for this dispatch (sourced from the
        related-repos registry). A resolver that supports plugin injection
        should **stage** these payloads onto its target (over its own transport)
        and fold the resulting ``--plugin-dir`` args into the launch command,
        alongside any provider-intrinsic plugins it resolves itself. The
        SpawnTarget is fully built at resolve time (``session_manager`` spawns it
        with no further resolver access), so this is the injection point for
        ``type="command"`` providers whose launch command is otherwise opaque to
        the bridge. Resolvers that do not support plugins may ignore it; the
        bridge only passes a non-empty set to resolvers that opt in.

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

    @property
    def bare_addressable(self) -> bool:
        """Whether this namespace participates in bare-name resolution.

        Discovery namespaces (``codespace:``, ``container:``) expose agents
        that exist *only* under their prefix, so a bare name should match
        them. A **modifier** namespace like ``admin:`` instead mirrors every
        existing static agent under the same base name to wrap it (elevation);
        letting it contribute bare-name candidates would make every local
        agent ambiguous with its own elevated twin and unreachable by bare
        name. Such resolvers return ``False`` so ``admin:`` stays strictly
        opt-in (you must type the ``admin:`` prefix to elevate).
        """
        return True

    async def ensure_ready(self, name: str) -> None:
        """Pre-flight check: ensure the target is ready for a session.

        Optional hook called before ``resolve()``. Implementations may
        start a shutdown codespace, wait for SSH, run health checks, etc.
        The default implementation is a no-op.

        Raises:
            RuntimeError: Target cannot be made ready.
        """

    async def target_repo(self, name: str) -> str | None:
        """The workspace repo (``owner/name``) this target hosts, or ``None``.

        Optional hook used by agent-bridge to source **related-repo** plugins:
        the bridge maps this repo to the control-plane ``related.yaml`` entry and
        passes that entry's plugins to :meth:`resolve` as ``extra_plugins``. A
        resolver that hosts a known repo (e.g. a CodeSpace's repository) should
        return it; the default returns ``None`` (no related-repo injection).
        """
        return None


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
            requires_admin=bool(config.get("requires_admin")),
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
            requires_admin=bool(
                project_data.get("requires_admin") or project_data.get("elevated")
            ),
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

    # Try exact key match first
    machine = machines.get(hostname)
    if machine:
        return machine, platform

    # Then a case-insensitive key match, then the explicit ``hostname`` field
    # (a machine keyed by a friendly name declares its raw COMPUTERNAME via
    # ``hostname:`` so it still self-detects on the box).
    for key, mc in machines.items():
        if key.lower() == hostname:
            return mc, platform
    for mc in machines.values():
        if getattr(mc, "hostname", "") and mc.hostname.lower() == hostname:
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
        if agent.auto_discovered or agent.derived or agent.project != local_agent.project:
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


def _short_machine_agent_name(machine: MachineConfig, env: SshEnvironment) -> str:
    """Friendly agent name for a control-plane (machine, env) pair.

    Derives from the machine's short ``display_name`` (e.g. ``dev6``) plus an
    environment suffix: the primary env (windows/linux) keeps the bare name,
    ``wsl`` appends ``-wsl``, any other env appends ``-<name>``. Reproduces the
    historic ``dev6`` / ``dev6-wsl`` / ``cloud1`` names once the machine
    ``display_name`` is the short colloquial form.
    """
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
    """Resolve a related.yaml ``locus.machines`` short name to a MachineConfig.

    ``related.yaml`` uses short names (``dev6``, ``cloud1``); machine keys are
    the full hostnames (``host-dev6``). Match by display_name, key, or the
    key with a leading ``<prefix>-`` stripped.
    """
    sl = short.strip().lower()
    if not sl:
        return None
    for m in machines.values():
        if (m.display_name or "").strip().lower() == sl:
            return m
        kl = m.key.lower()
        if kl == sl or kl.rsplit("-", 1)[-1] == sl or kl.endswith("-" + sl):
            return m
    return None


def _split_repo_venue(agent_name: str) -> tuple[str | None, str]:
    """Split a ``<repo>@<venue>`` address into ``(repo, venue)``.

    The repo dimension is orthogonal to the venue (machine / codespace /
    container): ``SPO.Core@dev6`` runs the SPO.Core binstub on the dev6 venue,
    ``example-web@<codespace>`` targets example-web's workspace on that codespace. A
    name with no ``@`` (or a leading/trailing empty side) is a bare venue and
    yields ``(None, agent_name)`` -- unchanged behavior. The venue half may
    itself be namespaced (e.g. ``example-web@codespace:foo``); only the first
    ``@`` is the separator.
    """
    if "@" in agent_name:
        repo, _, venue = agent_name.partition("@")
        repo, venue = repo.strip(), venue.strip()
        if repo and venue:
            return repo, venue
    return None, agent_name


def _load_related_entries(repo_root: Path) -> list[tuple[str, list[str], str]]:
    """Parse ``<repo>/.agent-worktrees/related.yaml`` minimally.

    Returns ``(name, locus_machines, delegate_via)`` tuples. Avoids importing
    agent_worktrees (a separate venv); reads only the fields synthesis needs.
    """
    p = repo_root / ".agent-worktrees" / "related.yaml"
    if not p.exists():
        return []
    try:
        import yaml
        data = yaml.safe_load(p.read_text(encoding="utf-8")) or {}
    except Exception as exc:
        log.warning("Failed to parse related.yaml at %s: %s", p, exc)
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
            [str(m).strip() for m in raw_machines if str(m).strip()]
            if isinstance(raw_machines, list) else []
        )
        delegate = entry.get("delegate")
        if isinstance(delegate, dict):
            delegate = delegate.get("via", "")
        delegate = str(delegate or "").strip().lower()
        out.append((str(name), machines, delegate))
    return out


def _agent_worktrees_bin() -> str | None:
    """Resolve the local ``agent-worktrees`` binstub, or None.

    Uses ``shutil.which`` first (honors PATHEXT so a Windows ``.cmd`` shim is
    found), then a ``$HOME/.local/bin`` fallback -- the same resolution order the
    remote ``agent-ssh explore`` probe uses, so local and remote introspection
    agree on what "installed" means.
    """
    import shutil
    exe = shutil.which("agent-worktrees")
    if exe:
        return exe
    base = Path.home() / ".local" / "bin"
    for cand in ("agent-worktrees.cmd", "agent-worktrees"):
        p = base / cand
        if p.exists():
            return str(p)
    return None


def load_local_repos() -> list[dict]:
    """Live-query the local per-machine repo registry (normalized).

    Runs ``agent-worktrees repos list --json`` -- the machine's own source of
    truth for checkout locations plus the per-repo ``agent`` flag -- and returns
    its ``repos`` list (``{name, class, remote, agent, paths, ...}``). This is the
    same normalized shape ``agent-ssh explore`` reads over SSH, kept live (no
    cache): the locations fall out of the machine, not a hand-maintained copy.

    Returns ``[]`` if the binstub is absent or the query fails, so callers simply
    fall back to prior behavior (no roster derived from repos.yaml).

    The binstub launches ``agent-worktrees`` in its **own** interpreter, so the
    child env is scrubbed of the parent's virtual-env markers
    (``VIRTUAL_ENV`` / ``PYTHONHOME`` / ``__PYVENV_LAUNCHER__`` / ``PYTHONPATH``).
    Without this, an agent-bridge running from its own venv leaks its interpreter
    context into the child, which (with a uv-managed Python) trips an
    ``_sre`` "SRE module mismatch" and makes this silently return ``[]``.
    """
    import subprocess

    exe = _agent_worktrees_bin()
    if not exe:
        log.debug("agent-worktrees binstub not found -- no local repo registry")
        return []
    creationflags = (
        subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0  # type: ignore[attr-defined]
    )
    child_env = {
        k: v
        for k, v in os.environ.items()
        if k not in ("VIRTUAL_ENV", "PYTHONHOME", "__PYVENV_LAUNCHER__", "PYTHONPATH")
    }
    try:
        proc = subprocess.run(  # noqa: S603 -- fixed argv, exe via shutil.which
            [exe, "repos", "list", "--json"],
            capture_output=True,
            text=True,
            timeout=15,
            check=False,
            creationflags=creationflags,
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
    """Infer the control-plane project from the live per-machine repo registry.

    The control-plane project is the ``agent: true`` repo whose checkout **owns**
    the loaded ``machines.yaml`` -- i.e. the topology file lives inside that
    repo's checkout path. This lets the roster binding fall out of two facts
    already true (the machine has the control repo checked out, with the agent
    flag, at a known path; and that checkout contains the topology file), so a
    machine that is reachable and has an agent-backing checkout is addressable
    with **no** hand-wired ``control_plane.project``.

    ``repos`` is the normalized registry (see :func:`load_local_repos`). Matching
    is by longest owning path so a nested checkout wins over an ancestor. Returns
    ``None`` when no agent-backing repo owns the topology file -- callers then
    keep prior behavior or honor an explicit ``control_plane.project``.
    """
    try:
        mpath = Path(machines_yaml_path).expanduser().resolve()
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
            cp = _resolve(raw)
            if cp is None:
                continue
            if mpath == cp or cp in mpath.parents:
                plen = len(str(cp))
                if plen > best_len:
                    best_name, best_len = name, plen
    return best_name


def derive_topology_agents(
    machines: dict[str, MachineConfig],
    control_plane_project: str | None,
    related: list[tuple[str, list[str], str]],
    local_machine: MachineConfig | None,
    local_platform: str = "",
    repos: list[dict] | None = None,
) -> dict[str, AgentConfig]:
    """Synthesize the agent roster from topology (machines × repos × envs).

    Replaces the hand-authored ``acp-agents.json`` static registry. Produces:

    1. **Control-plane machine agents** -- for the ``control_plane.project`` repo,
       one agent per (machine, SSH environment): ``dev6`` / ``dev6-wsl`` /
       ``cloud1``. Local same-platform env resolves to loopback; others to SSH.
    2. **Related-repo remote agents** -- for each ``related.yaml`` entry that
       delegates via ``agent-bridge``, one ``<repo>@<machine>`` agent per
       **remote** machine in its ``locus.machines`` (local ones are already
       covered by projects.yaml auto-discovery).
    3. **Repo-registry agents** (``repos`` -- the normalized live registry from
       :func:`load_local_repos`) -- for **each** ``agent: true`` repo checked out
       on the **local** machine, one ``<repo>@<machine>`` agent. This surfaces the
       machine's whole agent-backing set in the roster (parity with
       ``agent-ssh explore``'s derived agents), keyed by repo **name** so it holds
       even when ``machines.yaml`` is loaded from a worktree (a sibling of the
       anchor path the registry records). ``control_plane.project`` /
       ``related.yaml`` / explicit entries remain overrides -- emitted first, and
       this source uses ``setdefault`` semantics.

    Only **reachable** agents are emitted: an (machine, env) pair is reachable
    if it is local loopback (this machine + this platform) *or* the machine is
    ``ssh.ready``. With the inter-machine SSH mesh retired (issue #168) that
    leaves only the local loopback agents; remote agents reappear automatically
    once a machine's ``ssh.ready`` flips back to true.
    """
    out: dict[str, AgentConfig] = {}

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
                    continue  # unreachable (SSH mesh retired) -- skip
                name = _short_machine_agent_name(machine, env)
                if name in out:
                    name = f"{name}-{(env.name or '').lower()}"
                out[name] = AgentConfig(
                    name=name,
                    host=machine.key,
                    ssh_environment=env.name or None,
                    project=control_plane_project,
                    derived=True,
                    display_name=f"{machine.display_name} [{env.name}]",
                    description=(
                        f"Control-plane '{control_plane_project}' on "
                        f"{machine.display_name} ({env.name}) [derived from topology]"
                    ),
                )

    for repo, r_machines, delegate in related:
        if delegate != "agent-bridge":
            continue
        for short in r_machines:
            machine = _match_machine_shortname(machines, short)
            if not machine:
                continue
            if local_machine and machine.key == local_machine.key:
                continue  # local related repo -> covered by projects.yaml discovery
            if not machine.ssh_ready:
                continue  # remote + not SSH-ready -> unreachable (skip)
            name = f"{repo}@{machine.display_name}"
            if name in out:
                continue
            env = machine.get_spawnable_ssh_env() or (
                machine.ssh_environments[0] if machine.ssh_environments else None
            )
            out[name] = AgentConfig(
                name=name,
                host=machine.key,
                ssh_environment=(env.name if env else None),
                project=repo,
                derived=True,
                display_name=name,
                description=(
                    f"'{repo}' on {machine.display_name} [derived from related.yaml]"
                ),
            )

    # 3. Repo-registry agents -- every ``agent: true`` checkout on the local
    #    machine, as <repo>@<machine>. Surfaces the machine's full agent-backing
    #    set in the roster (not just the control-plane venue), keyed by repo name
    #    so it holds even for a worktree-loaded machines.yaml. Reachability-gated.
    if local_machine and repos:
        env = (
            local_machine.get_ssh_env(local_platform) if local_platform else None
        )
        reachable = bool(
            (env and _is_loopback(local_machine, env)) or local_machine.ssh_ready
        )
        if reachable:
            venue = local_machine.display_name or local_machine.key
            spawn_env = env or local_machine.get_spawnable_ssh_env()
            env_name = spawn_env.name if spawn_env else None
            for entry in repos:
                if not isinstance(entry, dict) or not entry.get("agent"):
                    continue
                repo = str(entry.get("name", "")).strip()
                if not repo:
                    continue
                name = f"{repo}@{venue}"
                if name in out:
                    continue  # control_plane / related / explicit entry wins
                out[name] = AgentConfig(
                    name=name,
                    host=local_machine.key,
                    ssh_environment=env_name,
                    project=repo,
                    derived=True,
                    display_name=name,
                    description=(
                        f"'{repo}' on {local_machine.display_name} "
                        "[derived from repos.yaml agent-backing checkout]"
                    ),
                )

    return out


def build_resolver(cfg) -> AgentResolver | None:  # noqa: ANN001
    """Build an AgentResolver from config profiles + local discovery.

    For each topology profile, loads its machines.yaml and **derives** the agent
    roster from topology (machines × repos × environments) -- see
    :func:`derive_topology_agents`. This replaces the hand-authored
    ``acp-agents.json``; a profile's ``agents_config`` is still honored if set
    (deprecated, explicit-wins back-compat). Auto-discovered local agents
    (projects.yaml) are merged last; explicit/derived entries win.

    Args:
        cfg: Loaded BridgeConfig with topologies dict.

    Returns:
        AgentResolver if any agents or machines were found, else None.
    """
    from .topology import load_control_plane_project, load_machines_yaml

    all_machines: dict[str, MachineConfig] = {}
    all_agents: dict[str, AgentConfig] = {}

    for _profile_name, profile in cfg.topologies.items():
        if not profile.machines_yaml:
            # No machines.yaml -- only an explicit (deprecated) agents_config.
            if profile.agents_config:
                all_agents.update(load_agent_registry(profile.agents_config))
            continue
        machines = load_machines_yaml(profile.machines_yaml)
        all_machines.update(machines)
        # Deprecated back-compat: honor an explicit acp-agents.json if still set;
        # explicit entries win over derived ones below.
        if profile.agents_config:
            all_agents.update(load_agent_registry(profile.agents_config))
        # Derive the roster from topology (replaces acp-agents.json). The local
        # per-machine repo registry (agent flag + checkout paths) is live-queried
        # once and reused for both control-plane inference and the per-repo
        # <repo>@<machine> derivation below.
        local_repos = load_local_repos()
        cp_project = load_control_plane_project(profile.machines_yaml)
        cp_source = "machines.yaml"
        if not cp_project:
            # No hand-wired binding: infer the control-plane repo from the live
            # per-machine repo registry (the agent flag + checkout paths). A
            # machine that has the control repo checked out (agent-backing) is
            # thus addressable without control_plane.project being set.
            cp_project = infer_control_plane_project(
                local_repos, profile.machines_yaml,
            )
            cp_source = "repos.yaml (agent flag)"
        if cp_project:
            log.info(
                "Control-plane project '%s' (from %s)", cp_project, cp_source,
            )
        repo_root = Path(profile.machines_yaml).expanduser().resolve().parent
        related = _load_related_entries(repo_root)
        local_machine, local_platform = _detect_local_machine(machines)
        derived = derive_topology_agents(
            machines, cp_project, related, local_machine, local_platform,
            local_repos,
        )
        for name, agent in derived.items():
            all_agents.setdefault(name, agent)  # explicit agents_config wins

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
            "Resolver built: %d machines, %d agents "
            "(%d derived, %d auto-discovered)",
            len(all_machines), len(all_agents),
            sum(1 for a in all_agents.values() if a.derived),
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


def register_credential_sources(builder) -> None:
    """Auto-discover and inject credential-relay sources from optional providers.

    Twin of :func:`_register_namespace_resolvers`: each provider plugin exposes a
    ``relay_provider.register_relay(builder)`` hook that contributes the
    credential sources (and policy/port) its targets need. agent-bridge owns and
    runs the relay; providers only inject their per-target profile. Import
    failures are logged and skipped -- providers are optional.

    ``builder`` is a :class:`credential_relay.registry.RelayBuilder`.
    """
    # codespace targets -- GitHub Codespaces (agent-codespaces package)
    try:
        from agent_codespaces.relay_provider import register_relay

        register_relay(builder)
        log.info("Registered credential-relay sources (agent-codespaces)")
    except ImportError:
        log.debug("agent-codespaces not installed -- no codespace relay sources")
    except Exception:
        log.warning("Failed to register agent-codespaces relay sources", exc_info=True)

    # container targets -- local Docker dev containers (agent-containers package)
    try:
        from agent_containers.relay_provider import register_relay as register_containers

        register_containers(builder)
        log.info("Registered credential-relay sources (agent-containers)")
    except ImportError:
        log.debug("agent-containers not installed -- no container relay sources")
    except Exception:
        log.warning("Failed to register agent-containers relay sources", exc_info=True)


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

    async def resolve_async(
        self, agent_name: str, sender_repo: str | None = None,
    ) -> SpawnTarget:
        """Resolve an agent name to a SpawnTarget (async path).

        Supports both regular agents and namespaced agents
        (``prefix:name``). For namespaced agents, calls
        ``ensure_ready()`` then ``resolve()`` on the namespace resolver.

        ``sender_repo`` (optional) is the repo the *caller* is dispatching from
        (derived by the CLI via ``agent-worktrees get project`` in its CWD). It
        supplies the **bare-venue default** for a machine venue, which carries no
        venue-default of its own: a bare ``dev6`` from an SPO.Core session runs
        ``SPO.Core@dev6`` rather than the control-plane fallback. Venues that
        *do* declare a default (a CodeSpace's own repo) ignore it.

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
            return await self._resolve_with_plugins(resolver, name)

        # ``<repo>@<venue>`` -- an explicit repo bound to a venue. If the full
        # name is itself an explicit registry entry (e.g. a ``<repo>@<machine>``
        # agent derived from the machine's repo registry or a ``related.yaml``
        # locus), resolve it directly -- it already carries its host/env/project
        # and needs no bare venue agent to rebind onto. Otherwise resolve the
        # venue and run <repo> there instead of the venue's default repo.
        repo, venue = _split_repo_venue(agent_name)
        if repo is not None:
            if agent_name in self._agents:
                return self._resolve_static(agent_name)
            return await self._resolve_venue_bound(repo, venue)

        # Bare name (no prefix): search static/provider agents AND every
        # namespace (codespaces, containers, ...) for a match by name or alias.
        # A single match resolves; multiple matches across namespaces are a
        # collision the caller must disambiguate (#50).
        candidates = await self._gather_bare_candidates(agent_name)
        if len(candidates) > 1:
            raise AmbiguousAgentError(
                agent_name, [qualified for qualified, _, _ in candidates]
            )
        if len(candidates) == 1:
            _, resolver, resolve_name = candidates[0]
            if resolver is None:
                # Bare **machine venue** with a known sender repo: machines carry
                # no venue-default, so run the sender's repo there instead of the
                # derived control-plane fallback (venue-default-else-sender).
                cfg = self._agents.get(resolve_name)
                if (
                    sender_repo
                    and cfg is not None
                    and cfg.derived
                    and cfg.host
                    and sender_repo != cfg.project
                ):
                    log.info(
                        "Bare machine venue '%s' -> sender repo '%s' "
                        "(venue-default-else-sender)",
                        resolve_name, sender_repo,
                    )
                    return await self._resolve_venue_bound(sender_repo, resolve_name)
                return await self._resolve_bare(agent_name)
            await resolver.ensure_ready(resolve_name)
            return await self._resolve_with_plugins(resolver, resolve_name)

        # No match anywhere -- defer to static resolution for its precise
        # "not found in registry" error.
        return self._resolve_static(agent_name)

    async def _resolve_venue_bound(
        self, repo: str, venue: str,
    ) -> SpawnTarget:
        """Resolve ``<repo>@<venue>``: the venue, bound to run ``<repo>``.

        - **machine / local** venues (loopback or SSH): the venue supplies the
          machine + environment; the target is rebound to run ``<repo>``'s
          binstub instead of the venue's default project. ``SPO.Core@dev6`` ->
          the SPO.Core binstub on dev6 (loopback).
        - **codespace / container** venues: ``repo`` is handed to the namespace
          resolver, which launches that repo's workspace on the venue -- landing
          in ``/workspaces/<basename(repo)>`` by convention and cloning it from
          ``repo_remote`` if the checkout is missing (#174).

        ``repo_remote`` is resolved once here from the repos registry (best
        effort -- ``None`` for a repo not in the registry, which is fine for a
        venue folder the bootstrap already owns).
        """
        repo_remote = resolve_repo_remote(repo)
        ns = self._parse_namespaced_agent(venue)
        if ns:
            prefix, name = ns
            resolver = self._namespace_resolvers[prefix]
            await resolver.ensure_ready(name)
            return await self._resolve_with_plugins(
                resolver, name, repo=repo, repo_remote=repo_remote,
            )

        candidates = await self._gather_bare_candidates(venue)
        if len(candidates) > 1:
            raise AmbiguousAgentError(
                venue, [qualified for qualified, _, _ in candidates]
            )
        if len(candidates) == 1:
            _, resolver, resolve_name = candidates[0]
            if resolver is None:
                target = await self._resolve_bare(venue)
                return self._bind_repo(target, repo, venue)
            await resolver.ensure_ready(resolve_name)
            return await self._resolve_with_plugins(
                resolver, resolve_name, repo=repo, repo_remote=repo_remote,
            )

        # No venue match -- resolve statically for a precise not-found error.
        target = self._resolve_static(venue)
        return self._bind_repo(target, repo, venue)

    def _bind_repo(self, target: SpawnTarget, repo: str, venue: str) -> SpawnTarget:
        """Rebind a machine/local venue target to run ``<repo>``'s binstub.

        A ``command`` (provider) target owns its own checkout layout and cannot
        be rebound here -- reaching this with one means the resolver did not
        accept a ``repo`` kwarg, so cross-repo dispatch to that venue is
        unsupported.
        """
        if target.type in ("local", "ssh"):
            import dataclasses
            return dataclasses.replace(target, project=repo)
        raise ValueError(
            f"Cross-repo dispatch '{repo}@{venue}' is not supported for this "
            "venue (it hosts its own repo/checkout)."
        )

    async def _resolve_with_plugins(
        self, resolver: "NamespaceResolver", name: str,
        repo: str | None = None, repo_remote: str | None = None,
    ) -> SpawnTarget:
        """Resolve via a namespace resolver, injecting related-repo plugins.

        agent-bridge *owns* the related-repo plugin set (sourced from the
        related-repos registry, ``related.yaml``); the resolver *folds + stages*
        it. We only pass ``extra_plugins`` / ``repo`` / ``repo_remote`` when
        applicable so resolvers that have not adopted those kwargs keep working
        unchanged.
        """
        extra = await self._related_plugins_for(resolver, name)
        return await self._call_resolver(
            resolver, name, extra_plugins=extra, repo=repo,
            repo_remote=repo_remote,
        )

    async def _call_resolver(
        self, resolver: "NamespaceResolver", name: str, *,
        extra_plugins: list[PluginRef], repo: str | None,
        repo_remote: str | None = None,
    ) -> SpawnTarget:
        """Invoke ``resolver.resolve`` passing only the kwargs it accepts.

        ``extra_plugins`` and ``repo_remote`` are optional (back-compat -- passed
        only when the resolver's signature declares them). ``repo`` is required
        to be honored when requested: if the resolver's ``resolve`` does not
        accept a ``repo`` kwarg, cross-repo dispatch to that venue is unsupported
        and we raise rather than silently launching the venue's default repo.
        """
        import inspect
        sig = inspect.signature(resolver.resolve)
        kwargs: dict[str, Any] = {}
        if extra_plugins and "extra_plugins" in sig.parameters:
            kwargs["extra_plugins"] = extra_plugins
        if repo is not None:
            if "repo" not in sig.parameters:
                raise ValueError(
                    f"Cross-repo dispatch (repo='{repo}') is not supported by "
                    f"the '{getattr(resolver, 'prefix', '?')}:' resolver."
                )
            kwargs["repo"] = repo
        if repo_remote is not None and "repo_remote" in sig.parameters:
            kwargs["repo_remote"] = repo_remote
        return await resolver.resolve(name, **kwargs)

    async def _related_plugins_for(
        self, resolver: "NamespaceResolver", name: str
    ) -> list[PluginRef]:
        """Related-repo plugins to inject for a dispatch target, or ``[]``.

        Asks the resolver for the target's workspace repo (optional
        ``target_repo`` hook) and looks up that repo's related-repo ``plugins``
        in the control-plane ``related.yaml``. Always fail-safe: any error (no
        hook, unknown repo, unreadable config) yields ``[]`` -- never raises
        into the dispatch path.
        """
        try:
            repo = await self._resolver_target_repo(resolver, name)
            if not repo:
                return []
            from .related_plugins import related_plugins_for_repo

            refs = related_plugins_for_repo(repo)
            if refs:
                log.info(
                    "Injecting %d related-repo plugin(s) for %s (repo=%s): %s",
                    len(refs), name, repo, [r.source for r in refs],
                )
            return refs
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("related-repo plugin sourcing failed for %s: %s", name, exc)
            return []

    async def _resolver_target_repo(
        self, resolver: "NamespaceResolver", name: str
    ) -> str | None:
        """Best-effort workspace repo for a resolved target via the optional
        ``target_repo`` hook (sync or async). ``None`` if unimplemented."""
        fn = getattr(resolver, "target_repo", None)
        if fn is None:
            return None
        result = fn(name)
        if inspect.isawaitable(result):
            result = await result
        return result if isinstance(result, str) and result.strip() else None

    async def _resolve_bare(self, agent_name: str) -> SpawnTarget:
        """Resolve a bare static/provider agent, routing elevated ones.

        A ``requires_admin`` agent is relayed to the elevated sub-daemon
        (Capability 2) when this daemon is non-elevated; otherwise it falls
        through to normal static resolution.
        """
        relay = await self._maybe_elevated_relay(agent_name)
        if relay is not None:
            return relay
        return self._resolve_static(agent_name)

    async def _maybe_elevated_relay(
        self, agent_name: str,
    ) -> SpawnTarget | None:
        """Return a sub-daemon relay target for an elevated agent, else None.

        Applies only to a registered ``requires_admin`` agent, on Windows,
        when this daemon is not itself elevated (the elevated sub-daemon
        resolves such agents locally via the sync path, so it never recurses
        here). Ensuring the sub-daemon is up can prompt for UAC and block, so
        it runs off the event loop.
        """
        from . import elevated

        config = self._agents.get(agent_name)
        if config is None:
            config = self._live_provider_agents().get(agent_name)
        if config is None or not config.requires_admin:
            return None
        if not elevated.relay_applicable(config.requires_admin):
            return None

        loop = asyncio.get_running_loop()
        token = await loop.run_in_executor(None, elevated.ensure_running)
        cmd = elevated.relay_spawn_command(config.name, token=token)
        log.info(
            "Routing elevated agent '%s' via sub-daemon relay (port %d)",
            config.name, elevated.ELEVATED_PORT,
        )
        return SpawnTarget(
            type="command", spawn_command=cmd, project=config.project,
        )

    async def _gather_bare_candidates(
        self, name: str
    ) -> list[tuple[str, "NamespaceResolver | None", str]]:
        """Find every agent a bare name matches, across static + namespaces.

        Returns ``(qualified_name, resolver_or_None, resolve_name)`` tuples:
        ``resolver`` is None for static/provider agents (resolved via
        :meth:`_resolve_static`); otherwise it is the namespace resolver and
        ``resolve_name`` is the raw name to hand it. ``qualified_name`` is what
        the collision message enumerates (``prefix:name`` for namespace agents,
        the bare name for static ones).
        """
        candidates: list[tuple[str, "NamespaceResolver | None", str]] = []

        # Static / provider agents have no namespace prefix.
        if name in self._agents or name in self._live_provider_agents():
            candidates.append((name, None, name))

        lname = name.lower()
        for prefix, resolver in self._namespace_resolvers.items():
            # Modifier namespaces (e.g. admin:) mirror existing static agents
            # under the same base name; they must not contribute bare-name
            # candidates or every local agent collides with its elevated twin.
            if not getattr(resolver, "bare_addressable", True):
                continue
            try:
                infos = await resolver.list()
            except Exception:
                log.warning(
                    "Namespace resolver '%s' failed to list during bare-name "
                    "resolution of '%s'", prefix, name, exc_info=True,
                )
                continue
            for info in infos:
                names = [info.name, *getattr(info, "aliases", [])]
                if any(n and n.lower() == lname for n in names):
                    candidates.append((f"{prefix}:{info.name}", resolver, info.name))

        return candidates

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
                codespace=config.codespace,
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

        # Loopback detection: if the resolved machine is the local machine
        # and the SSH environment matches our platform, spawn locally instead
        # of SSH-ing to ourselves. SSH loopback causes binstub stdout
        # pollution that breaks ACP JSON-RPC parsing. This runs *before* the
        # ssh_ready gate so local dispatch works with the SSH mesh retired.
        if (
            ssh_env
            and self._local_machine
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

        # Real SSH is required (remote machine, or cross-environment on the
        # local box). Enforce SSH-readiness here -- *after* loopback detection,
        # so a local same-platform agent still dispatches even when the machine
        # is marked ssh_ready=false (the inter-machine SSH mesh being retired
        # must not disable local loopback dispatch). See issue #168.
        if not machine.ssh_ready:
            raise ValueError(
                f"Machine '{machine.key}' is not marked as SSH-ready "
                "in the topology (inter-machine SSH is unavailable; only "
                "local loopback dispatch works)"
            )

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

    def _is_local_loopback_agent(self, config: AgentConfig) -> bool:
        """True when this agent dispatches via local loopback rather than SSH.

        A control-plane agent derived from topology carries ``host`` = the
        machine key plus an ``ssh_environment``; when that host is the *local*
        machine and the env matches our platform, ``resolve()`` short-circuits
        to a local spawn (see the loopback branch there). Such an agent must
        not be advertised as a remote SSH target -- doing so mislabels a
        reachable local agent as an unreachable SSH one once the inter-machine
        SSH mesh is retired (``ssh.ready: false``). See issue #168.
        """
        return bool(
            config.host
            and self._local_machine
            and config.host == self._local_machine.key
            and config.ssh_environment == self._local_platform
        )

    def _agent_to_dict(self, config: AgentConfig) -> dict[str, Any]:
        """Convert an AgentConfig to API-ready dict."""
        spawnable = not config.managed
        if config.spawn_command:
            target_type = "command"
        elif config.host and not self._is_local_loopback_agent(config):
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
            "derived": config.derived,
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
                        "aliases": [
                            f"{prefix}:{a}" for a in getattr(agent, "aliases", [])
                        ],
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
                        "bare_addressable": getattr(
                            resolver, "bare_addressable", True
                        ),
                        "state": agent.state,
                    })
            except Exception:
                log.warning(
                    "Namespace resolver '%s' failed to list agents",
                    prefix, exc_info=True,
                )

        return result
