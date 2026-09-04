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
import secrets
import shutil
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from dataclasses import dataclass, field, replace
from pathlib import Path
from typing import Any

from agent_procutil import no_window_flags
from dropin_registry import Finding, WarningTracker

from .provider_sources import ProviderManifest, scan_provider_registry
from .topology import MachineConfig, SshEnvironment
from .transport import PluginRef, SpawnTarget

log = logging.getLogger("agent-bridge")

_PROJECTS_YAML_DEFAULT = "~/.agent-worktrees/projects.yaml"
_REPOS_YAML_DEFAULT = "~/.agent-worktrees/repos.yaml"


def _normalize_repo_basename(name: str) -> str:
    """Normalize a repo name/key to its comparable basename.

    Strips any ``owner/`` prefix, lowercases, and folds ``.`` to ``-`` so that
    registry keys and lookups match regardless of case or ``.``/``-`` spelling
    (e.g. ``your-org/Example.Marketplace`` matches ``example-marketplace``).
    """
    return name.strip().lower().split("/")[-1].replace(".", "-")


def resolve_repo_remote(repo: str) -> str | None:
    """Resolve a logical repo name to its git remote URL.

    Reads the agent-worktrees global repos registry
    (``~/.agent-worktrees/repos.yaml``, override via ``AGENT_WORKTREES_REPOS_YAML``)
    and returns ``repos.<repo>.remote``. Used to thread a ``repo_remote`` into a
    ``<repo>@<venue>`` dispatch so a venue that hosts by convention (a CodeSpace's
    ``/workspaces/<basename>`` layout, #174) can clone the repo if it is missing.

    Matching is exact on the registry key first, then a case-insensitive fallback
    on the key's basename (so ``example-web`` matches an ``example-web`` entry regardless
    of case, and ``.``/``-`` spellings are folded together). Returns ``None`` when the
    registry is absent/unparseable or the repo
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
        want = _normalize_repo_basename(repo)
        for key, val in repos.items():
            if not isinstance(val, dict):
                continue
            if _normalize_repo_basename(str(key)) == want:
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
    aliases: list[str] = field(default_factory=list)
    icon: str | None = None
    worktree_root: str | None = None
    env: dict[str, str] = field(default_factory=dict)
    project: str | None = None  # agent-worktrees project (binstub name)
    # Whether this agent is enumerated as a worktree-discovery *lane* (its
    # machine's worktrees listed under it in /api/v1/worktrees). Default True for
    # a machine/repo lane. Set False for a **spawn body** that legitimately needs
    # ``project`` to be embodied into a worktree on spawn (e.g. a headless pool
    # worker) but shares another agent's host+root -- otherwise it double-lists
    # that machine's entire inventory under itself. A spawn body's real sessions
    # already appear under the machine lane it runs on.
    worktree_discovery: bool = True
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


class AgentRegistryLoadError(ValueError):
    """A configured explicit agent registry is missing or invalid."""


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


# Cross-plugin exit-code contract for the ``namespace-resolve`` CLI seam
# (#892 Inc 3): a provider's ``namespace-resolve`` maps its resolver's
# ``KeyError`` (not found) -> exit 3 and ``ValueError`` (bad state) -> exit 4, so
# the process boundary preserves those distinct outcomes. Kept in sync with each
# provider's ``_NS_NOT_FOUND_EXIT`` / ``_NS_BAD_STATE_EXIT``.
_NS_NOT_FOUND_EXIT = 3
_NS_BAD_STATE_EXIT = 4


class CliNamespaceResolver(NamespaceResolver):
    """Drive a namespace provider over a **process boundary** (#892 Inc 3).

    Implements the :class:`NamespaceResolver` interface by shelling out to the
    provider's binstub (``<binstub> namespace-list/-resolve/-target-repo/
    -ensure-ready``) instead of importing the provider's resolver in the bridge
    venv -- so a provider fix reaches dispatch from the provider's OWN venv with
    no agent-bridge redeploy (retires the vendoring-drift class for the resolver
    seam). Falls back to an in-process ``fallback`` resolver on any *subprocess*
    failure (binstub absent, unexpected non-zero, unparseable output), so
    resolution can never regress while the venvs are still coupled. A provider's
    *legitimate* not-found (exit 3) / bad-state (exit 4) is mapped back to
    ``KeyError`` / ``ValueError`` -- not treated as a failure -- so the boundary
    preserves the resolver contract.
    """

    def __init__(
        self, prefix: str, binstub: str,
        fallback: NamespaceResolver | None = None,
        *, command: list[str] | None = None,
    ) -> None:
        self._prefix = prefix
        self._binstub = binstub
        self._fallback = fallback
        # An explicit, already-resolved argv prefix (e.g. from a providers.d
        # manifest). When set it bypasses ``shutil.which(binstub)`` so the
        # daemon -- whose venv/PATH cannot see the provider's binstub -- can
        # still drive the provider over the process boundary.
        self._command = list(command) if command else None

    @property
    def prefix(self) -> str:
        return self._prefix

    @property
    def bare_addressable(self) -> bool:
        if self._fallback is not None:
            return self._fallback.bare_addressable
        return True

    async def _run(
        self, argv: list[str], *, timeout: float = 90.0,
    ) -> tuple[int, str, str] | None:
        """Run ``<binstub> <argv...>``; return ``(rc, stdout, stderr)`` or
        ``None`` when the process could not be launched (binstub absent / spawn
        error) -- the signal to fall back to the in-process resolver."""
        if self._command:
            cmd = [*self._command, *argv]
        else:
            exe = shutil.which(self._binstub)
            if not exe:
                return None
            cmd = [exe, *argv]

        def _call() -> subprocess.CompletedProcess[str]:
            creationflags = no_window_flags()
            return subprocess.run(
                cmd, capture_output=True, text=True, timeout=timeout,
                creationflags=creationflags,
            )

        try:
            r = await asyncio.to_thread(_call)
            return r.returncode, r.stdout, r.stderr
        except Exception:
            log.debug(
                "CLI namespace call failed: %s %s", self._binstub, argv,
                exc_info=True,
            )
            return None

    async def _fallback_or_raise(self, method: str, *args, **kwargs):
        if self._fallback is None:
            raise RuntimeError(
                f"{self._binstub} {method} unavailable and no in-process "
                f"fallback resolver for namespace '{self._prefix}:'"
            )
        fn = getattr(self._fallback, method)
        # Pass only the kwargs the fallback actually declares -- a provider's
        # resolver may have a narrower signature than this generic shim (e.g. the
        # container resolver's ``resolve(self, name)`` accepts no cross-repo /
        # plugin kwargs), so forwarding them blindly would ``TypeError`` (#892
        # Inc 3b). Mirrors agent-bridge's own resolve-kwarg introspection.
        if kwargs:
            try:
                params = inspect.signature(fn).parameters
                kwargs = {k: v for k, v in kwargs.items() if k in params}
            except (TypeError, ValueError):
                kwargs = {}
        return await fn(*args, **kwargs)

    async def list(self) -> list[NamespaceAgentInfo]:
        res = await self._run(["namespace-list"])
        if res is not None and res[0] == 0:
            try:
                return [NamespaceAgentInfo(**d) for d in json.loads(res[1])]
            except Exception:
                log.warning(
                    "namespace-list output unparseable (%s) -- falling back",
                    self._binstub, exc_info=True,
                )
        if self._fallback is None:
            # A missing/unreachable provider binstub with no in-process fallback
            # means "this provider contributes no dynamic agents here" -- a
            # benign, expected state, NOT an error. The elevated sub-daemon, for
            # example, cannot see the ``agent-codespaces`` binstub on its PATH,
            # so a strict raise here produced a scary RuntimeError traceback on
            # every agent-list. Degrade to empty so enumeration stays clean.
            # (resolve/ensure_ready still raise -- you cannot spawn what you
            # cannot resolve.)
            log.debug(
                "namespace '%s:': provider '%s' unavailable and no in-process "
                "fallback -- contributing no dynamic agents",
                self._prefix, self._binstub,
            )
            return []
        return await self._fallback_or_raise("list")

    async def resolve(
        self, name: str, *, extra_plugins: "list[PluginRef]" = (),
        repo: str | None = None, repo_remote: str | None = None,
    ) -> SpawnTarget:
        """Resolve via the CLI seam (full-capability signature).

        Declares the cross-repo / plugin kwargs so agent-bridge's resolve-kwarg
        introspection (``inspect.signature``) offers cross-repo dispatch to this
        namespace. A provider whose venues do NOT support cross-repo registers
        via :class:`RestrictedCliNamespaceResolver` (narrower signature) instead.
        """
        return await self._resolve_impl(
            name, extra_plugins=extra_plugins, repo=repo, repo_remote=repo_remote,
        )

    async def _resolve_impl(
        self, name: str, *, extra_plugins: "list[PluginRef]" = (),
        repo: str | None = None, repo_remote: str | None = None,
    ) -> SpawnTarget:
        argv = ["namespace-resolve", name]
        if repo:
            argv += ["--repo", repo]
        if repo_remote:
            argv += ["--repo-remote", repo_remote]
        for p in extra_plugins or ():
            src = getattr(p, "source", None)
            if src:
                argv += ["--stage-plugin", src]
        res = await self._run(argv)
        if res is not None:
            rc, out, err = res
            if rc == _NS_NOT_FOUND_EXIT:
                raise KeyError(err.strip() or name)
            if rc == _NS_BAD_STATE_EXIT:
                raise ValueError(err.strip() or f"{name} is not spawnable")
            if rc == 0:
                try:
                    spec = json.loads(out)
                except Exception:
                    log.warning(
                        "namespace-resolve output unparseable (%s) -- falling "
                        "back", self._binstub, exc_info=True,
                    )
                else:
                    # Preserve the provider-owned venue contract as the
                    # authoritative metadata. Older providers expose only the
                    # top-level workspace/profile pair. Duplicate compatibility
                    # fields must agree; trust conflicts resolve only toward the
                    # restricted posture.
                    raw_venue = spec.get("venue")
                    if raw_venue is not None and not isinstance(raw_venue, dict):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned a "
                            "non-object venue contract"
                        )
                    venue = dict(raw_venue or {})
                    ws = spec.get("workspace_folder")
                    prof = spec.get("security_profile")
                    if ws is not None and (
                        not isinstance(ws, str) or not ws.strip()
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned an "
                            "invalid workspace_folder"
                        )
                    if prof is not None and prof not in {"trusted", "restricted"}:
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned an "
                            "invalid security_profile"
                        )
                    for key in (
                        "provider",
                        "kind",
                        "target_id",
                        "scope",
                        "fleet",
                        "workspace_folder",
                        "security_profile",
                        "configured_security_profile",
                        "observed_security_profile",
                        "effective_security_profile",
                        "state",
                        "transport",
                    ):
                        value = venue.get(key)
                        if value is not None and (
                            not isinstance(value, str) or not value.strip()
                        ):
                            raise RuntimeError(
                                f"{self._binstub} namespace-resolve venue.{key} "
                                "must be a non-empty string"
                            )
                    instance_id = venue.get("instance_id")
                    if instance_id is not None and (
                        not isinstance(instance_id, str) or not instance_id.strip()
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve venue.instance_id "
                            "must be null or a non-empty string"
                        )
                    for key in ("ready", "posture_verified"):
                        if key in venue and not isinstance(venue[key], bool):
                            raise RuntimeError(
                                f"{self._binstub} namespace-resolve venue.{key} "
                                "must be boolean"
                            )
                    if "schema_version" in venue and (
                        not isinstance(venue["schema_version"], int)
                        or isinstance(venue["schema_version"], bool)
                        or venue["schema_version"] < 1
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve "
                            "venue.schema_version must be a positive integer"
                        )
                    capabilities = venue.get("capabilities")
                    if capabilities is not None and (
                        not isinstance(capabilities, dict)
                        or not all(
                            isinstance(capability, str)
                            and capability
                            and isinstance(enabled, bool)
                            for capability, enabled in capabilities.items()
                        )
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve "
                            "venue.capabilities must be boolean flags"
                        )
                    if ws:
                        venue_ws = venue.get("workspace_folder")
                        if venue_ws and venue_ws != ws:
                            raise RuntimeError(
                                f"{self._binstub} namespace-resolve returned "
                                "conflicting workspace_folder values"
                            )
                        venue["workspace_folder"] = ws
                    if prof:
                        venue_prof = venue.get("security_profile")
                        if venue_prof and venue_prof != prof:
                            if "restricted" not in {venue_prof, prof}:
                                raise RuntimeError(
                                    f"{self._binstub} namespace-resolve returned "
                                    "conflicting security_profile values"
                                )
                            venue["security_profile"] = "restricted"
                            venue["ready"] = False
                        else:
                            venue["security_profile"] = prof
                    target_type = spec.get("type", "command")
                    spawn_command = spec.get("spawn_command")
                    if target_type != "command":
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned "
                            f"unsupported target type {target_type!r}"
                        )
                    if (
                        not isinstance(spawn_command, list)
                        or not spawn_command
                        or not all(
                            isinstance(part, str) and part and "\x00" not in part
                            for part in spawn_command
                        )
                    ):
                        raise RuntimeError(
                            f"{self._binstub} namespace-resolve returned an "
                            "invalid spawn_command"
                        )
                    return SpawnTarget(
                        type=target_type,
                        spawn_command=spawn_command,
                        user=spec.get("user"),
                        codespace=spec.get("codespace"),
                        container=spec.get("container"),
                        venue=venue or None,
                    )
            # any other non-zero -> a CLI failure, not a resolver outcome
        return await self._fallback_or_raise(
            "resolve", name, extra_plugins=extra_plugins, repo=repo,
            repo_remote=repo_remote,
        )

    async def ensure_ready(self, name: str) -> None:
        res = await self._run(["namespace-ensure-ready", name])
        if res is None:
            await self._fallback_or_raise("ensure_ready", name)
            return
        rc, _out, err = res
        if rc == 0:
            return
        # A ran-but-non-zero ensure-ready is the authoritative "not ready".
        raise RuntimeError(err.strip() or f"{self._prefix}:{name} is not ready")

    async def target_repo(self, name: str) -> str | None:
        res = await self._run(["namespace-target-repo", name])
        if res is not None and res[0] == 0:
            return res[1].strip() or None
        if self._fallback is not None:
            return await self._fallback.target_repo(name)
        return None


class RestrictedCliNamespaceResolver(CliNamespaceResolver):
    """A :class:`CliNamespaceResolver` for a namespace whose venues do **not**
    support cross-repo dispatch or plugin injection (#892 Inc 3b).

    Overrides :meth:`resolve` with a **narrow** ``resolve(self, name)`` signature
    so agent-bridge's resolve-kwarg introspection (``inspect.signature``)
    correctly reports that ``repo`` / ``repo_remote`` / ``extra_plugins`` are
    unsupported -- exactly as the underlying in-process resolver (e.g. the
    container resolver's ``resolve(self, name)``) already signals. The subprocess
    logic is inherited via :meth:`_resolve_impl`.
    """

    async def resolve(self, name: str) -> SpawnTarget:  # type: ignore[override]
        return await self._resolve_impl(name)


def parse_agent_registry(data: dict[str, Any]) -> dict[str, AgentConfig]:
    """Parse raw acp-agents.json data into typed AgentConfig objects."""
    registry: dict[str, AgentConfig] = {}
    for name, config in data.items():
        raw_aliases = config.get("aliases", [])
        if (
            not isinstance(raw_aliases, list)
            or any(not isinstance(alias, str) for alias in raw_aliases)
        ):
            raise ValueError(
                f"agent {name!r} aliases must be a list of strings"
            )
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
            worktree_discovery=bool(config.get("worktree_discovery", True)),
            setup_script=config.get("setup_script"),
            requires_admin=bool(config.get("requires_admin")),
        )
    return registry


def load_agent_registry(
    path: str | Path, *, strict: bool = False,
) -> dict[str, AgentConfig]:
    """Load and parse an agent registry file (acp-agents.json)."""
    p = Path(path).expanduser()
    if not p.exists():
        message = f"agent registry not found at {p}"
        if strict:
            raise AgentRegistryLoadError(message)
        log.warning(message)
        return {}
    try:
        data = json.loads(p.read_text(encoding="utf-8")) or {}
        registry = parse_agent_registry(data)
        log.info("Loaded %d agents from %s", len(registry), p)
        return registry
    except (OSError, json.JSONDecodeError, AttributeError, TypeError, ValueError) as exc:
        message = f"failed to parse agent registry at {p}: {exc}"
        if strict:
            raise AgentRegistryLoadError(message) from exc
        log.error(message)
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


def load_elevated_projects() -> set[str]:
    """Return the set of adopted project names that require elevation.

    Reads the agent-worktrees ``projects.yaml`` and collects every project
    flagged ``elevated: true`` (or the legacy ``requires_admin: true``).
    Elevation is an **intrinsic property of the repo** (e.g. SPO.Core's
    base-repo enlistment needs an admin shell), so the derived
    ``<repo>@<machine>`` topology agent must be born ``requires_admin`` from
    this same source -- not patched after the fact. Kept independent of
    :func:`discover_local_agents` so the derivation (which runs first) can
    consult it without depending on agent-discovery ordering or on a project
    being ``expose_agent``-visible.

    Fail-safe: returns an empty set if pyyaml or projects.yaml is unavailable.
    """
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

    The fallback is **platform-ordered**: on POSIX the extension-less binstub
    only, on Windows the ``.cmd`` shim first. ``~/.local/bin`` carries *all* of
    ``agent-worktrees`` (POSIX shell), ``agent-worktrees.cmd`` (DOS batch), and
    ``agent-worktrees.ps1`` side by side, so a naive ``.cmd``-first fallback
    hands a Linux caller the Windows batch file -> ``Exec format error`` when it
    is run. This bit the daemon specifically: a systemd user service whose PATH
    omits ``~/.local/bin`` misses on ``shutil.which`` and falls through here.
    """
    import shutil
    exe = shutil.which("agent-worktrees")  # marketplace-isolation: allow agent-worktrees-management
    if exe:
        return exe
    base = Path.home() / ".local" / "bin"
    cands = ("agent-worktrees.cmd", "agent-worktrees") if os.name == "nt" \
        else ("agent-worktrees",)
    for cand in cands:
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
    creationflags = no_window_flags()
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
    elevated_projects: set[str] | None = None,
    *,
    default_copilot_args: list[str] | None = None,
    default_env: dict[str, str] | None = None,
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
                    aliases=(
                        [env.alias]
                        if env.alias and env.alias != name
                        else []
                    ),
                    description=(
                        f"Control-plane '{control_plane_project}' on "
                        f"{machine.display_name} ({env.name})"
                        f"{_machine_metadata(machine)} [derived from topology]"
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

    # 3. Repo-registry agents -- every ``agent: true`` checkout on the local
    #    machine, as <repo>@<machine>. Surfaces the machine's full agent-backing
    #    set in the roster (not just the control-plane venue), keyed by repo name
    #    so it holds even for a worktree-loaded machines.yaml. Reachability-gated.
    if local_machine and repos:
        elevated_projects = elevated_projects or set()
        env = (
            local_machine.get_ssh_env(local_platform) if local_platform else None
        )
        reachable = bool(
            (env and _is_loopback(local_machine, env)) or local_machine.ssh_ready
        )
        if reachable:
            spawn_env = env or local_machine.get_spawnable_ssh_env()
            venue = local_machine.display_name or local_machine.key
            stable_venue = (
                spawn_env.alias
                if spawn_env and spawn_env.alias
                else local_machine.key
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
                    continue  # control_plane / related / explicit entry wins
                out[name] = AgentConfig(
                    name=name,
                    host=local_machine.key,
                    ssh_environment=env_name,
                    project=repo,
                    derived=True,
                    display_name=name,
                    aliases=(
                        [f"{repo}@{stable_venue}"]
                        if stable_venue != venue
                        else []
                    ),
                    # Elevation is intrinsic to the repo (e.g. a base-repo
                    # enlistment needing an admin shell): a repo adopted
                    # ``elevated`` in projects.yaml is born ``requires_admin``
                    # here, so its derived <repo>@<machine> agent routes through
                    # the elevated sub-daemon just like the bare project agent.
                    requires_admin=repo in elevated_projects,
                    description=(
                        f"'{repo}' on {local_machine.display_name}"
                        f"{_machine_metadata(local_machine)} "
                        "[derived from repos.yaml agent-backing checkout]"
                    ),
                )

    # Apply the profile's multi-machine system-wide spawn defaults to every derived agent.
    # Derived agents otherwise carry no copilot args/env, so the model target had
    # to be repeated on each hand-authored acp-agents.json entry. Applying it here
    # lets the derived roster be the single source of the machine lanes. A derived
    # agent never sets these itself, so this is a plain fill (no merge ambiguity);
    # an explicit agents_config entry is a *separate* agent that still wins by
    # ``setdefault`` in :func:`build_resolver`.
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
    """Resolve the spawn defaults for a profile: machine-local wins, else in-repo.

    ``profile`` is the machine-local :class:`TopologyProfile`; ``repo_cfg`` is the
    optional in-repo :class:`RepoBridgeConfig` (``None`` when the repo carries no
    ``.agent-bridge/config.yaml``). Each dimension resolves independently: if the
    machine-local profile **explicitly set** the field it wins -- *including setting
    it to empty*, so a local profile can deliberately clear a repo-provided default
    (``default_env: {}``); otherwise the repo-declared default is used; otherwise
    empty. "Explicitly set" is distinguished from "left at its default" via
    Pydantic's ``model_fields_set``, so an absent local value falls through to the
    repo, but a present-but-empty one overrides it."""
    profile_set = getattr(profile, "model_fields_set", set())

    def _pick(field: str, repo_value: object) -> object:
        if field in profile_set:
            return getattr(profile, field)
        return repo_value if repo_cfg is not None else getattr(profile, field)

    copilot_args = _pick(
        "default_copilot_args", repo_cfg.default_copilot_args if repo_cfg else None
    )
    env = _pick("default_env", repo_cfg.default_env if repo_cfg else None)
    return list(copilot_args), dict(env)


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
            # No machines.yaml -- only an explicit (deprecated) agents_config.
            if profile.agents_config:
                agents_path = Path(profile.agents_config).expanduser()
                try:
                    all_agents.update(load_agent_registry(agents_path, strict=True))
                except AgentRegistryLoadError as exc:
                    topology_errors.append(
                        f"{profile_name}: {exc}"
                    )
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
        # Deprecated back-compat: honor an explicit acp-agents.json if still set;
        # explicit entries win over derived ones below.
        if profile.agents_config:
            agents_path = Path(profile.agents_config).expanduser()
            try:
                all_agents.update(load_agent_registry(agents_path, strict=True))
            except AgentRegistryLoadError as exc:
                topology_errors.append(
                    f"{profile_name}: {exc}"
                )
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
        # In-repo config (<repo>/.agent-bridge/config.yaml) carries the multi-machine system
        # spawn defaults *in the repo* so they ride to every machine on sync. The
        # machine-local topology profile still wins when it sets a default (an
        # explicit local override); otherwise the repo-declared default is used.
        from .config import load_repo_bridge_config

        repo_cfg = load_repo_bridge_config(repo_root)
        eff_copilot_args, eff_env = _effective_spawn_defaults(profile, repo_cfg)
        derived = derive_topology_agents(
            machines, cp_project, related, local_machine, local_platform,
            local_repos, load_elevated_projects(),
            default_copilot_args=eff_copilot_args,
            default_env=eff_env,
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

    if all_machines or all_agents or topology_errors:
        resolver = AgentResolver(
            all_agents, all_machines, topology_errors=topology_errors,
        )
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


def daemon_resolver(cfg) -> AgentResolver:  # noqa: ANN001
    """Resolver for the long-running daemon -- ALWAYS returns one.

    Unlike :func:`build_resolver` (which returns ``None`` when there is no static
    topology), the daemon must always hold a resolver so declarative namespace
    providers (``codespace:``, ``container:``) discovered from
    ``~/.agent-bridge/providers.d/`` can attach and be enumerated/dispatched even
    on a box with **no** ``machines.yaml``/topology (the golden path). When there
    is no static topology, an empty resolver is stood up and its namespace
    resolvers registered, so ``codespace:<name>`` works without any topology.
    """
    resolver = build_resolver(cfg)
    if resolver is None:
        resolver = AgentResolver({}, {})
        _register_namespace_resolvers(resolver)
    return resolver


def _register_namespace_resolvers(resolver: AgentResolver) -> None:
    """Register namespace resolvers: declarative providers + built-in ``admin:``.

    External providers (codespace:, container:, ...) are discovered from the
    ``~/.agent-bridge/providers.d/`` manifest registry via
    :meth:`AgentResolver.refresh_provider_resolvers` -- a provider self-registers
    by dropping a manifest from its own bootstrap hook, so the daemon needs
    neither the provider package importable nor its binstub on ``PATH``. The
    built-in ``admin:`` modifier is registered directly (it is part of
    agent-bridge itself, not an external provider).
    """
    # External providers from the declarative providers.d registry.
    resolver.refresh_provider_resolvers(force=True)

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


class FileTokenValidator:
    """A file-backed relay-token validator (#892 Inc 2).

    **Byte-equivalent** to the in-process provider validators
    (``agent_codespaces.relay_token.validate`` /
    ``agent_containers.relay_provider._validate``): reads a JSON ``{name: token}``
    store and accepts ``token`` iff it matches any stored value under
    :func:`secrets.compare_digest`. An empty token, a missing / unreadable /
    malformed store, or a non-string stored value never matches. This lets
    agent-bridge apply a provider's ``get-azure-token`` gate over a **process
    boundary** -- reading the SAME host-side token file the provider's own
    ``token_for`` writes -- with no in-process import of the provider. The gate
    can only regress if this diverges from the providers, so a golden equivalence
    test pins it against their exact logic across a token matrix.
    """

    __slots__ = ("_path",)

    def __init__(self, path: str | Path) -> None:
        self._path = Path(path)

    def __call__(self, token: str) -> bool:
        if not token:
            return False
        try:
            data = json.loads(self._path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        for v in data.values():
            secret = v if isinstance(v, str) else (
                v.get("token") if isinstance(v, dict) else None
            )
            if not isinstance(secret, str) or not secret:
                # Structured entries store the secret under ``token``; anything
                # else (or a legacy non-string value) can never match.
                continue
            try:
                if secrets.compare_digest(token, secret):
                    return True
            except TypeError:
                # compare_digest rejects non-ASCII str -- a non-ASCII token can
                # never match an (ASCII hex) stored secret, so treat it as a
                # no-match. The providers' validators would *raise* here; both
                # net to "rejected", so this is a hardening, never a weakening.
                return False
        return False


class FileTokenAuthorizer:
    """A file-backed, request-scoped relay-token authorizer.

    The scoped counterpart to :class:`FileTokenValidator`: reads the same JSON
    token store, but each entry may be a structured ``{"token", "repository",
    "allowed_resources"}`` record. For a gated ``get-azure-token`` request it
    accepts the presented ``token`` iff it matches a stored secret AND the
    requested ``scope``/``resource`` is in that entry's ``allowed_resources``.
    Mirrors ``agent_codespaces.relay_token.authorize_azure`` over the process
    boundary, so agent-bridge enforces a *per-token* Azure scope from the
    provider's own token file with no in-process import. Legacy string-only
    entries carry no allowlist and fall back to the profile's static
    ``azure_resources`` allowlist (the pre-scoping behavior) -- never a
    regression for tokens that already worked.
    """

    __slots__ = ("_path", "_static")

    def __init__(
        self, path: str | Path, static_resources: list[str] | None = None,
    ) -> None:
        self._path = Path(path)
        self._static = {
            str(r).removesuffix("/.default").rstrip("/")
            for r in (static_resources or [])
        }

    def __call__(self, token: str, action: str, fields: dict[str, str]) -> bool:
        if action != "get-azure-token" or not token:
            return False
        try:
            data = json.loads(self._path.read_text(encoding="utf-8")) or {}
        except (OSError, json.JSONDecodeError):
            return False
        if not isinstance(data, dict):
            return False
        requested = fields.get("scope") or fields.get("resource") or ""
        normalized = requested.removesuffix("/.default").rstrip("/")
        for entry in data.values():
            secret = entry if isinstance(entry, str) else (
                entry.get("token") if isinstance(entry, dict) else None
            )
            if not isinstance(secret, str) or not secret:
                continue
            try:
                if not secrets.compare_digest(token, secret):
                    continue
            except TypeError:
                return False
            if isinstance(entry, dict) and "allowed_resources" in entry:
                allowed = {
                    str(v).removesuffix("/.default").rstrip("/")
                    for v in entry.get("allowed_resources", [])
                }
            else:
                allowed = self._static
            return "*" in allowed or normalized in allowed
        return False


def _relay_source_by_name(name: str):
    """Construct a shared ``credential_relay`` source by its profile name.

    agent-bridge owns the relay, so it builds the named sources itself (rather
    than importing them from a provider). Unknown names are skipped by the caller.
    """
    from credential_relay.sources.gh_auth import GhAuthSource
    from credential_relay.sources.git_credential import GitCredentialSource

    factories = {
        "git-credential": GitCredentialSource,
        "gh-auth": GhAuthSource,
    }
    ctor = factories.get(name)
    return ctor() if ctor is not None else None


def _apply_relay_profile(builder, profile: dict) -> None:
    """Apply a declarative provider relay profile to ``builder`` (#892 Inc 2).

    Mirrors what a provider's in-process ``register_relay`` does, but the token
    gate is applied via a :class:`FileTokenValidator` bound to the profile's
    ``token_store`` (the provider's host-side token file) rather than the
    provider's in-process validator function.
    """
    for sname in profile.get("sources", []):
        src = _relay_source_by_name(sname)
        if src is not None:
            builder.add_source(src)
        else:
            log.warning("Unknown relay source '%s' in profile -- skipping", sname)
    builder.set_port(profile.get("port"))          # None -> no-op
    builder.set_ado_host(profile.get("ado_host"))  # None -> no-op
    if "azure_resources" in profile:
        builder.allow_azure_resources(list(profile["azure_resources"] or []))
    gated = profile.get("gated_actions") or []
    store = profile.get("token_store")
    if gated and store:
        if profile.get("scoped_azure"):
            # Per-token scope enforcement: the provider's token store records an
            # ``allowed_resources`` allowlist per token, so gate get-azure-token
            # with the request-scoped authorizer instead of a bare validator.
            builder.authorize_token(
                list(gated),
                FileTokenAuthorizer(store, profile.get("azure_resources")),
            )
        else:
            builder.require_token(list(gated), FileTokenValidator(store))


def _relay_profile_via_cli(binstub: str) -> dict | None:
    """Fetch a provider's declarative relay profile via ``<binstub> relay-profile``.

    Returns the parsed profile dict, or ``None`` when the binstub is absent / the
    call fails / the output is unparseable -- the signal to fall back to the
    in-process ``register_relay`` import.
    """
    exe = shutil.which(binstub)
    if not exe:
        return None
    creationflags = no_window_flags()
    try:
        r = subprocess.run(
            [exe, "relay-profile"], capture_output=True, text=True, timeout=20,
            creationflags=creationflags,
        )
    except Exception:
        log.debug("relay-profile CLI failed for %s", binstub, exc_info=True)
        return None
    if r.returncode != 0 or not r.stdout.strip():
        return None
    try:
        data = json.loads(r.stdout)
        return data if isinstance(data, dict) else None
    except Exception:
        log.warning(
            "relay-profile output unparseable (%s)", binstub, exc_info=True,
        )
        return None


def _register_provider_relay(
    builder, binstub: str,
) -> None:
    """Register one provider's relay profile via its ``relay-profile`` CLI seam.

    Process-boundary **only** (#1643): apply the declarative profile from the
    provider's ``relay-profile`` CLI with a file-backed token validator, so a
    provider relay fix reaches the daemon from its OWN venv (no bridge redeploy).
    There is **no** in-process ``register_relay`` import fallback: the daemon runs
    from its own isolated venv where a provider package is neither importable nor
    on ``PATH`` (see :mod:`agent_bridge.provider_sources`). When the binstub is
    absent or its CLI fails, the provider simply contributes no relay sources.
    """
    profile = _relay_profile_via_cli(binstub)
    if profile is None:
        log.debug("%s relay-profile unavailable -- no relay sources", binstub)
        return
    try:
        _apply_relay_profile(builder, profile)
        log.info("Applied credential-relay profile (%s, CLI seam)", binstub)
    except Exception:
        log.warning(
            "Failed applying %s relay profile", binstub, exc_info=True,
        )


def register_credential_sources(builder) -> None:
    """Auto-discover and inject credential-relay sources from optional providers.

    Twin of :func:`_register_namespace_resolvers`: each provider contributes the
    credential sources (and policy/port/token gate) its targets need. agent-bridge
    owns and runs the relay; providers only inject their per-target profile. This
    is driven **purely over a process boundary** (#1643): the provider's
    ``relay-profile`` CLI + a file-backed token validator, with **no** in-process
    ``register_relay`` import -- the daemon never imports a provider package.

    ``builder`` is a :class:`credential_relay.registry.RelayBuilder`.
    """
    _register_provider_relay(builder, "agent-codespaces")
    _register_provider_relay(builder, "agent-containers")


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
        *,
        topology_errors: list[str] | None = None,
        topology_warnings: list[str] | None = None,
    ) -> None:
        self._agents = agents
        self._machines = machines
        self._topology_errors = list(topology_errors or [])
        self._topology_warnings = list(topology_warnings or [])
        self._namespace_resolvers: dict[str, NamespaceResolver] = {}
        # Throttle for the declarative providers.d re-scan (monotonic seconds).
        self._provider_scan_ts: float = 0.0
        self._provider_scan_ttl: float = 10.0
        self._provider_entries: dict[str, ProviderManifest] = {}
        self._provider_manifests: dict[str, ProviderManifest] = {}
        self._provider_namespaces: set[str] = set()
        self._provider_warning_tracker = WarningTracker()
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

        self._agent_alias_index: dict[str, str | None] = {}
        for canonical, config in agents.items():
            for alias in [canonical, *config.aliases]:
                key = alias.casefold()
                existing = self._agent_alias_index.get(key)
                if existing is None and key in self._agent_alias_index:
                    continue
                if existing is not None and existing != canonical:
                    self._agent_alias_index[key] = None
                    warning = (
                        f"agent alias {alias!r} is ambiguous between "
                        f"{existing!r} and {canonical!r}"
                    )
                    self._topology_warnings.append(warning)
                    log.warning(warning)
                else:
                    self._agent_alias_index[key] = canonical

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

    @property
    def topology_errors(self) -> list[str]:
        return list(self._topology_errors)

    @property
    def topology_warnings(self) -> list[str]:
        return list(self._topology_warnings)

    def canonical_agent_name(self, name: str) -> str | None:
        """Resolve an exact, case-insensitive, or declared static alias."""
        if name in self._agents:
            return name
        return self._agent_alias_index.get(name.casefold())

    def get_agent_config(self, name: str) -> AgentConfig | None:
        canonical = self.canonical_agent_name(name)
        return self._agents.get(canonical) if canonical else None

    def machine_key_for_agent(self, config: AgentConfig) -> str | None:
        """Return the normalized topology key hosting ``config``."""
        if config.host:
            try:
                machine, _env = self._resolve_machine(
                    config.host, config.ssh_environment,
                )
                return machine.key
            except ValueError:
                return None
        if config.auto_discovered and self._local_machine:
            return self._local_machine.key
        return None

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

    def refresh_provider_resolvers(self, *, force: bool = False) -> None:
        """Register namespace resolvers from the ``providers.d`` manifest registry.

        Scans ``~/.agent-bridge/providers.d/`` and reconciles a
        :class:`CliNamespaceResolver` (or :class:`RestrictedCliNamespaceResolver`
        when ``restricted``) to the current authoritative desired set. Removed,
        invalid, or missing-target manifests withdraw the dynamic resolver; an
        indeterminate registry scan retains the last-known set. Built-in
        resolvers are never replaced or removed. Throttled to at most once per
        ``_provider_scan_ttl`` seconds unless ``force``.
        """
        now = time.monotonic()
        if not force and (now - self._provider_scan_ts) < self._provider_scan_ttl:
            return
        self._provider_scan_ts = now

        try:
            report = scan_provider_registry(previous=self._provider_entries)
        except Exception:
            log.warning("Provider manifest discovery failed", exc_info=True)
            return

        findings = list(report.findings)
        for manifest in report.manifests.values():
            if (
                manifest.namespace in self._namespace_resolvers
                and manifest.namespace not in self._provider_namespaces
            ):
                findings.append(
                    Finding(
                        registry="providers.d",
                        entry=manifest.source_path,
                        status="inactive",
                        reason="duplicate",
                        target=manifest.namespace,
                        owner=manifest.plugin,
                        remedy="Remove the provider entry or rename its namespace.",
                        detail="namespace conflicts with a built-in resolver",
                    )
                )
        warning_batch = self._provider_warning_tracker.select(findings)
        for finding in warning_batch.emitted:
            target = f" target={finding.target}" if finding.target else ""
            log.warning(
                "%s: %s (%s)%s; run `agent-bridge doctor`",
                finding.registry,
                finding.entry,
                finding.reason,
                target,
            )
        if warning_batch.suppressed:
            log.warning(
                "providers.d: %d additional finding(s) suppressed; "
                "run `agent-bridge doctor`",
                warning_batch.suppressed,
            )
        if warning_batch.recovered:
            log.info(
                "providers.d: %d prior finding(s) recovered",
                warning_batch.recovered,
            )

        desired = dict(report.manifests)
        desired_namespaces = set(desired)

        for namespace in sorted(self._provider_namespaces - desired_namespaces):
            self.unregister_namespace_resolver(namespace)
            self._provider_manifests.pop(namespace, None)

        for manifest in desired.values():
            namespace = manifest.namespace
            current = self._provider_manifests.get(namespace)
            if current == manifest and namespace in self._provider_namespaces:
                continue
            if namespace in self._provider_namespaces:
                self.unregister_namespace_resolver(namespace)
                self._provider_namespaces.discard(namespace)
                self._provider_manifests.pop(namespace, None)
            elif namespace in self._namespace_resolvers:
                continue
            cls = (
                RestrictedCliNamespaceResolver
                if manifest.restricted
                else CliNamespaceResolver
            )
            try:
                self.register_namespace_resolver(
                    cls(
                        manifest.namespace,
                        manifest.command[0],
                        command=list(manifest.command),
                    )
                )
                self._provider_namespaces.add(namespace)
                self._provider_manifests[namespace] = manifest
                log.info(
                    "Registered %s: namespace resolver from %s",
                    namespace, manifest.source_path,
                )
            except Exception:
                log.warning(
                    "Failed to register '%s:' from %s",
                    manifest.namespace, manifest.source_path, exc_info=True,
                )
        self._provider_entries = dict(report.entries)

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

    def resolve_ssh_environment(
        self, host: str, ssh_environment: str | None = None
    ) -> tuple[MachineConfig, SshEnvironment]:
        """Resolve a topology machine key or SSH alias to one exact environment."""
        machine, forced_env = self._resolve_machine(host, ssh_environment)
        environment = forced_env or machine.get_ssh_env(ssh_environment)
        if environment is None:
            raise ValueError(
                f"Machine '{machine.key}' has no SSH environment"
            )
        return machine, environment

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
        # Pick up any provider manifest dropped since the last scan.
        self.refresh_provider_resolvers()
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
        # and needs no bare venue agent to rebind onto. A ``requires_admin``
        # entry (an elevated repo's derived local agent) is routed through the
        # elevated sub-daemon here, exactly as a bare elevated agent is -- so
        # ``SPO.Core@dev6`` elevates just like bare ``SPO.Core``. Otherwise
        # resolve the venue and run <repo> there instead of the venue's default.
        repo, venue = _split_repo_venue(agent_name)
        if repo is not None:
            static_name = self.canonical_agent_name(agent_name)
            if static_name:
                return await self._resolve_bare(static_name)
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
        canonical = self.canonical_agent_name(agent_name) or agent_name
        relay = await self._maybe_elevated_relay(canonical)
        if relay is not None:
            return relay
        target = self._resolve_static(canonical)
        config = self._agents.get(canonical)
        if config is not None and config.requires_admin:
            from . import elevated

            if elevated.is_process_elevated():
                return replace(target, elevated=True)
        return target

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
        if config is None or not config.requires_admin:
            return None
        if not elevated.relay_applicable(config.requires_admin):
            return None

        loop = asyncio.get_running_loop()
        token = await loop.run_in_executor(None, elevated.ensure_running)
        cmd = elevated.relay_spawn_command(config.name, token=token)
        log.info(
            "Routing elevated agent '%s' via sub-daemon relay (port %d)",
            config.name, elevated.discovered_port(),
        )
        return SpawnTarget(
            type="command",
            spawn_command=cmd,
            project=config.project,
            elevated=True,
        )

    async def _gather_bare_candidates(
        self, name: str
    ) -> list[tuple[str, "NamespaceResolver | None", str]]:
        """Find every agent a bare name matches, across static + namespaces.

        Returns ``(qualified_name, resolver_or_None, resolve_name)`` tuples:
        ``resolver`` is None for static agents (resolved via
        :meth:`_resolve_static`); otherwise it is the namespace resolver and
        ``resolve_name`` is the raw name to hand it. ``qualified_name`` is what
        the collision message enumerates (``prefix:name`` for namespace agents,
        the bare name for static ones).
        """
        candidates: list[tuple[str, "NamespaceResolver | None", str]] = []

        # Static agents have no namespace prefix.
        static_name = self.canonical_agent_name(name)
        if static_name:
            candidates.append((static_name, None, static_name))

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

    def _own_plugin_args(self, config) -> list[str]:
        """``--plugin-dir`` args for the launching repo's OWN enabledPlugins.

        A repo's ``.github/copilot/settings.json`` ``enabledPlugins`` load in a
        headless launch only when installed on disk; an *enabled-but-uninstalled*
        plugin (the fork / fresh-machine case) is silently skipped. This stages
        each such plugin per-launch via ``--plugin-dir`` -- **never** globally
        enabling it. Resolves the repo anchor from the project registry (or falls
        back to ``cwd``). Fail-safe -> ``[]`` (never breaks dispatch).
        """
        try:
            from pathlib import Path as _Path

            from .related_plugins import _registry_anchor
            from .repo_own_plugins import repo_plugin_dir_args

            anchor = None
            project = getattr(config, "project", None)
            if project:
                anchor = _registry_anchor(project)
            if anchor is None and getattr(config, "cwd", None):
                anchor = _Path(config.cwd)
            return repo_plugin_dir_args(anchor)
        except Exception as exc:  # pragma: no cover - defensive
            log.debug("own-plugin arg staging failed: %s", exc)
            return []

    def _resolve_static(self, agent_name: str) -> SpawnTarget:
        """Resolve via the static / auto-discovered registry."""
        canonical = self.canonical_agent_name(agent_name)
        config = self._agents.get(canonical) if canonical else None
        if not config:
            raise KeyError(f"Agent '{agent_name}' not found in registry")

        if config.managed:
            raise ValueError(
                f"Agent '{agent_name}' is managed (non-spawnable) -- "
                "it cannot be started via agent-bridge transport"
            )

        # Agents carrying an explicit spawn_command bypass topology resolution
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
                copilot_args=config.copilot_args + self._own_plugin_args(config),
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
                copilot_args=config.copilot_args + self._own_plugin_args(config),
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
            "aliases": list(config.aliases),
            "description": config.description or "",
            "icon": config.icon,
            "managed": config.managed,
            "spawnable": spawnable,
            "target_type": target_type,
            "host": config.host or "",
            "machine_key": self.machine_key_for_agent(config),
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

        Includes static and auto-discovered agents. Namespace agents (e.g. live
        codespaces) are NOT included here (they require async enumeration). Use
        :meth:`list_agents_async` for the full list.
        """
        result = []
        for config in self._agents.values():
            result.append(self._agent_to_dict(config))

        return result

    async def list_agents_async(self) -> list[dict[str, Any]]:
        """List all agents including namespace-resolved agents.

        Calls ``list()`` on each registered namespace resolver to
        include dynamically discovered agents (e.g. live codespaces).
        """
        self.refresh_provider_resolvers()
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
