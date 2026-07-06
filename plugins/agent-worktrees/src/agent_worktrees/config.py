"""Config loading and machine detection.

Reads per-project config from ~/.{project}/config.yaml and provides
typed access.  Runtime lives at ~/.agent-worktrees/ (shared across
projects); per-project state at ~/.{project}/.

The active project is resolved from the current working directory (git-like),
or an explicit ``--project``; it is threaded in-process, not read from
``$WORKTREE_PROJECT``.
"""

from __future__ import annotations

import os
import platform
import re
import socket
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

import yaml

_ENV_KEY_RE = re.compile(r"^[A-Za-z_][A-Za-z0-9_]*$")
_PROJECT_NAME_RE = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9._-]{0,63}$")

# In-repo, committed config carrying *repo-level policy/settings* (shared
# across every machine that checks out the repo), as opposed to the
# machine-local ``~/.{project}/config.yaml`` which carries machine-specific
# paths. It is the **base** layer for a repo's settings; the machine-local
# ``repos.<name>`` block overrides it per key. The schema is **flat
# repo-settings** (no ``anchor`` / ``worktree_root`` -- those are machine
# paths -- and no ``repos:`` map).
#
# Preferred location is the **directory form**
# ``<anchor>/.agent-worktrees/config.yaml``; the legacy single-file
# ``<anchor>/.agent-worktrees.yaml`` (which carried only a ``pr:`` block) is
# still read as a back-compat fallback.
INREPO_CONFIG_DIRNAME = ".agent-worktrees"        # <anchor>/.agent-worktrees/config.yaml
INREPO_CONFIG_FILENAME = ".agent-worktrees.yaml"  # legacy single-file fallback

# Global, machine-wide config: the user-owned BASE layer holding only
# machine-wide settings -- top-level ``srcroot`` / ``machine`` / ``platform`` /
# ``copilot_profiles`` / ``auto_fast_forward`` / ``headless``. It never carries
# per-repo settings or a registry of repos/machines; the full merged config for
# a target repo is computed on demand by ``load_config``. Lives at
# ``~/.agent-worktrees/config.yaml`` (the shared runtime root).
GLOBAL_CONFIG_FILENAME = "config.yaml"

@dataclass(frozen=True)
class CopilotProfile:
    """A named Copilot backend configuration."""

    name: str
    label: str
    env: dict[str, str] = field(default_factory=dict)
    copilot_args: list[str] = field(default_factory=list)


# Synthetic default when no profiles are configured.
DEFAULT_PROFILE = CopilotProfile(name="cloud", label="☁️  Cloud (GitHub)")


@dataclass(frozen=True)
class PRConfig:
    """Pull-request workflow configuration for a managed repo.

    When ``enabled`` is false (the default), the repo uses the direct-push
    finalization flow unchanged.  When enabled, finalization runs the
    PR-based flow (see ``docs/plans/pr-workflow.md`` in aperture-labs).

    ``required`` is the enforcement switch.  With ``enabled`` alone, PR mode
    is *available* but optional: ``push-changes``/``finalize`` only take the
    PR path once a PR record exists (a ``create-pr`` was run), otherwise they
    finalize direct-to-master.  With ``required: true`` the direct-to-master
    path is **refused** -- ``push-changes`` (and the unmerged-work guard in
    ``finalize``) will not push to the default branch; the only way to land
    work is ``create-pr`` -> open PR -> merge.  Setting ``required: true``
    implies ``enabled: true``.

    ``strategy`` selects the default *disposition* after ``create-pr`` --
    it does not control squash timing (squashing always happens at
    ``create-pr``):

    - ``detach``    -- finalize the worktree immediately; resume later via a
                       fresh ``create`` workflow if the PR needs more work.
    - ``keep-alive`` -- keep the worktree open to iterate on review feedback,
                        pushing updates to the feature branch.
    """

    enabled: bool = False
    required: bool = False         # enforce PRs: refuse direct-to-master
    provider: str = "gitea"        # gitea | github | azure-devops
    strategy: str = "detach"       # default disposition: keep-alive | detach
    branch_prefix: str = "feature"
    # ``head_scheme`` selects HOW create-pr publishes the PR head (#1815):
    #
    # - ``snapshot`` (default, legacy) -- create a separate local
    #   ``{prefix}/<slug>`` branch at the squashed commit, reset the worktree
    #   base branch to upstream, and push that branch. HEAD returns to the
    #   worktree branch (#1804).
    # - ``refspec`` -- keep the squashed work ON ``worktree/<id>`` and push it
    #   directly to the PR head ref via a refspec (no local feature branch, no
    #   checkout dance; the worktree stays on its own branch). Feature-complete
    #   but **opt-in** until every PR-mode repo's pre-push hook allows the
    #   mediated refspec push (a facility hook that blocks ``worktree/*`` by ref
    #   name must honor ``AGENT_WORKTREES_PR_PUSH=1`` first -- see aperture-labs
    #   #1815). A parallel ``--new`` PR auto-falls-back to a snapshot ref.
    #
    # ``head_pattern`` is the PR head-name template (tokens ``{prefix}``,
    # ``{slug}``, ``{suffix}``, ``{username}``, ``{machine}``). Empty means the
    # scheme default: ``{prefix}/{slug}-{suffix}`` under ``snapshot``
    # (``feature/<slug>``) and ``pr/{slug}-{suffix}`` under ``refspec``.
    # Repos that want e.g. ``user/<username>/<slug>-<suffix>`` set it explicitly.
    head_scheme: str = "snapshot"  # snapshot | refspec
    head_pattern: str = ""         # empty -> scheme default (see above)
    # Provider-plugin settings (PR creation via a provider CLI). ``api_base``
    # is the hosting endpoint -- required for self-hosted Gitea
    # (e.g. https://host/gitea) and Azure DevOps org URLs; GitHub defaults to
    # the public API. ``token_command`` (a shell command that prints a token,
    # e.g. a vault fetch) takes precedence over ``token_env`` (an env var
    # name); GitHub falls back to ``gh`` auth when neither is set. ``labels``
    # are applied to every opened PR (``{machine}`` is templated). ``auto_open``
    # is **opt-in** (default False): only when a repo sets it true does
    # ``create-pr`` open the PR via the provider; otherwise the branch is just
    # pushed and PR creation is left to the agent (manual flow).
    api_base: str = ""
    token_env: str = ""
    token_command: str = ""
    labels: tuple[str, ...] = ()
    auto_open: bool = False        # opt-in: open the PR via the provider after push


@dataclass(frozen=True)
class RepoConfig:
    """Configuration for a single managed repository."""

    anchor: str
    worktree_root: str
    default_branch: str = "master"
    remote: str = "origin"
    launch: dict[str, list[str]] = field(default_factory=dict)
    launch_recovery: dict[str, list[str]] = field(default_factory=dict)
    setup_hook: dict[str, str] = field(default_factory=dict)
    """Optional repo **session setup hook**, keyed by platform ("windows" /
    "linux"). The value is a path to a script (relative to ``anchor`` unless
    absolute) that agent-worktrees' normalized launcher runs -- passing context
    by argument (``-Machine`` / ``-Recovery``), not ambient env -- *before* it
    execs Copilot. The hook does repo-specific work (vault, MCP) and returns; it
    does NOT launch Copilot itself. Declaring it opts the repo into the
    normalized launch flow (inverting the legacy ``setup.ps1``-as-launch)."""
    session_path: dict[str, list[str]] = field(default_factory=dict)
    """Optional directories the normalized launcher prepends to ``PATH`` before
    launch, keyed by platform. Each entry is templated (``{work_dir}``,
    ``{anchor}``, ``{machine}``, ``{repo_name}``) -- e.g.
    ``["{work_dir}/tools/bin"]``. The generic mechanism that lets a repo expose
    its tool binstubs without an ambient PATH export in a setup script."""
    session_env: dict[str, str] = field(default_factory=dict)
    """Optional environment variables the launch plan applies to the Copilot
    session (e.g. ``COPILOT_FEATURE_FLAGS``). Merged into the plan ``env`` by
    ``_build_env`` (below the profile, so a profile can override). This is how a
    repo contributes session env **without** an ambient export in a setup script
    -- and it works with the normalized launcher, where the repo setup hook runs
    as a child process and therefore cannot set env for the Copilot exec."""
    validate_paths: list[str] = field(default_factory=list)
    validate_hook: dict[str, list[str]] = field(default_factory=dict)
    service_paths: list[str] = field(default_factory=list)
    post_install_hook: dict[str, list[str]] = field(default_factory=dict)
    pr: PRConfig = field(default_factory=PRConfig)
    base_repo: bool = False
    """When true, this repo is driven in **base-repo (no-worktree)** mode: the
    anchor checkout is used directly and no worktree is ever created. Used to
    adopt repos that do not support worktrees (e.g. an enlistment-based monorepo)
    so agent-bridge can launch an ACP agent against the anchor via a custom
    ``launch`` command. Configured entirely from the user-local
    ``~/.<project>/config.yaml`` overlay -- nothing is written into the repo."""


@dataclass(frozen=True)
class Config:
    """Top-level project configuration."""

    srcroot: str
    machine: str
    platform: str
    repo_name: str = ""
    repos: dict[str, RepoConfig] = field(default_factory=dict)
    copilot_profiles: list[CopilotProfile] = field(default_factory=list)
    headless: bool = False
    """When true, the project is driven via CLI only -- its bare binstub
    invocation lists worktrees instead of launching an interactive Copilot
    session. Used to control external repos (e.g. copilot-extensions) whose
    worktree lifecycle is managed from another project's session."""
    auto_fast_forward: bool = True
    """When true (the default), resuming a clean worktree that is strictly
    behind its upstream default branch fast-forwards it before launch, so
    the session and setup script see an up-to-date tree.  Only ever a
    fast-forward (clean + no local commits ahead); dirty/ahead/diverged
    worktrees are left untouched.  Set false to opt out of auto-update."""
    new_picker: bool = False
    """When true, the bare binstub launches the overhauled Textual worktree
    picker instead of the legacy ANSI one.  Machine-wide opt-in during rollout
    (resolved machine-local > global).  The env vars still override:
    ``AGENT_WORKTREES_LEGACY_PICKER`` forces the legacy picker (rollback) and
    ``AGENT_WORKTREES_NEW_PICKER`` forces the new one."""

    @property
    def default_repo(self) -> RepoConfig:
        """Return the default repo for this project.

        Looks up ``self.repo_name`` in the repos map first, then falls
        back to the sole entry if there is exactly one repo.  Raises
        ``KeyError`` otherwise.
        """
        if self.repo_name in self.repos:
            return self.repos[self.repo_name]
        if len(self.repos) == 1:
            return next(iter(self.repos.values()))
        raise KeyError(
            f"No repo '{self.repo_name}' in config and multiple repos defined. "
            f"Available: {', '.join(self.repos)}"
        )

# --- Machine registry ---

@dataclass(frozen=True)
class SSHEnvironment:
    """An SSH environment for a machine (windows, wsl, linux)."""

    name: str
    alias: str
    shell: str = ""


@dataclass(frozen=True)
class MachineEntry:
    """A registered machine from machines.yaml."""

    key: str
    display_name: str
    environment: str
    alias: str = ""
    role: str = ""
    ssh_environments: list[SSHEnvironment] = field(default_factory=list)
    ssh_ready: bool = False
    copilot: bool = True


def load_machines_yaml(repo_dir: str | Path) -> dict[str, MachineEntry]:
    """Load the machine registry from ``machines.yaml`` in the repo root.

    Returns a dict mapping machine key → MachineEntry.
    Raises FileNotFoundError if machines.yaml is missing.
    """
    path = Path(repo_dir) / "machines.yaml"
    if not path.exists():
        raise FileNotFoundError(f"Machine registry not found at {path}")

    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    if not raw or "machines" not in raw:
        raise ValueError(f"machines.yaml at {path} is missing 'machines' key")

    entries: dict[str, MachineEntry] = {}
    for key, data in raw["machines"].items():
        if not isinstance(data, dict):
            continue
        ssh_envs: list[SSHEnvironment] = []
        ssh_block = data.get("ssh", {})
        for env in ssh_block.get("environments", []):
            if isinstance(env, dict) and "name" in env and "alias" in env:
                ssh_envs.append(SSHEnvironment(
                    name=env["name"], alias=env["alias"],
                    shell=env.get("shell", ""),
                ))
        entries[key] = MachineEntry(
            key=key,
            display_name=data.get("display_name", key),
            environment=data.get("environment", ""),
            alias=data.get("alias", ""),
            role=data.get("role", ""),
            ssh_environments=ssh_envs,
            ssh_ready=bool(ssh_block.get("ready", False)),
            copilot=bool(data.get("copilot", True)),
        )
    return entries


def machine_name(entry: MachineEntry) -> str:
    """Return the canonical name for a machine entry.

    Returns the alias if one is defined (the colloquial facility name),
    otherwise the key (which is the real hostname).
    """
    return entry.alias or entry.key


def find_machine_entry(
    entries: dict[str, MachineEntry], name: str,
) -> MachineEntry | None:
    """Look up a machine by key or alias (case-insensitive).

    Hostnames are case-insensitive, so match keys and aliases without regard
    to case (Windows reports COMPUTERNAME in mixed case but tooling often
    lowercases it). Returns None if no entry matches.
    """
    if name in entries:
        return entries[name]
    name_lower = name.lower()
    for key, entry in entries.items():
        if key.lower() == name_lower:
            return entry
        if entry.alias and entry.alias.lower() == name_lower:
            return entry
    return None


def detect_machine(repo_dir: str | Path | None = None) -> str:
    """Auto-detect machine name from hostname.

    If *repo_dir* is provided, reads ``machines.yaml`` and matches
    the hostname against machine keys and aliases (exact match).
    Returns the canonical name (alias if set, otherwise key).
    Falls back to the raw hostname if no registry is available.
    """
    hostname = socket.gethostname().lower()

    if repo_dir is not None:
        try:
            entries = load_machines_yaml(repo_dir)
            # Exact match on key (real hostname) first -- case-insensitive
            for key, entry in entries.items():
                if hostname == key.lower():
                    return machine_name(entry)
            # Then check aliases
            for entry in entries.values():
                if entry.alias and hostname == entry.alias.lower():
                    return machine_name(entry)
        except (FileNotFoundError, ValueError):
            pass  # no registry -- fall through to raw hostname

    return hostname


def render_copilot_instructions(
    entry: MachineEntry, project: str = "",
) -> str:
    """Render the content of ``machine.instructions.md`` for a machine.

    Detects the current platform and includes it along with the
    deployment environment (SSH alias) so agents know their exact
    identity for service deployments.  When *project* is provided,
    includes project and binstub metadata.
    """
    plat = detect_platform()

    # Find the SSH alias matching the current platform
    deploy_env = ""
    for ssh_env in entry.ssh_environments:
        if ssh_env.name == plat:
            deploy_env = ssh_env.alias
            break

    lines = [
        f"Machine: {entry.display_name}",
        f"Hostname: {entry.key}",
        f"Environment: {entry.environment}",
        f"Platform: {plat}",
    ]
    if deploy_env:
        lines.append(f"Deployment environment: {deploy_env}")
    if entry.role:
        lines.append(f"Role: {entry.role}")
    if project:
        lines.append(f"Project: {project}")
        lines.append(f"Binstub: {project}")
    return "\n".join(lines) + "\n"


def detect_platform() -> str:
    """Detect the current platform: 'windows', 'wsl', or 'linux'."""
    if platform.system() == "Windows":
        return "windows"
    # WSL detection
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


def _home() -> Path:
    """Cross-platform home directory."""
    if platform.system() == "Windows":
        return Path(os.environ.get("USERPROFILE", str(Path.home())))
    return Path.home()


# ── Active project (in-process, git-like context) ───────────────────────
# The active project is resolved once per invocation from the current working
# directory (or an explicit ``--project``) and threaded in-process here -- it is
# NOT read from ambient environment variables. ``main()`` sets it after CWD/flag
# resolution; every consumer reads it through ``project_name()``.
#
# There is deliberately no "assumed CWD" override: when ``--project`` targets a
# project the caller is not already inside, ``main()`` performs a real
# ``os.chdir`` to that project's anchor (git ``-C`` semantics), so *every* code
# path -- worktree-id inference, repo discovery, git subprocesses -- resolves
# consistently from the process's actual working directory.
_ACTIVE_PROJECT: str | None = None


def set_active_project(name: str | None) -> None:
    """Set the in-process active project (resolved from CWD or ``--project``)."""
    global _ACTIVE_PROJECT
    _ACTIVE_PROJECT = name.strip() if name else None


def active_project() -> str | None:
    """Return the in-process active project name, or ``None`` if unresolved."""
    return _ACTIVE_PROJECT


def project_name() -> str:
    """Return the in-process active project name (resolved from CWD or ``--project``).

    Resolution is git-like: the active project is derived from the current
    directory (or an explicit ``--project``) by ``main()`` and threaded in
    process -- it is **not** read from ``$WORKTREE_PROJECT``. Raises
    ``RuntimeError`` when no project could be resolved.
    """
    name = (_ACTIVE_PROJECT or "").strip()
    if not name:
        # Transitional fallback for internal / import-time callers that run
        # before main() resolves context (shell installers, module-import
        # side effects). Command dispatch always sets the active project from
        # CWD/--project first, so this never overrides CWD-first resolution.
        # Removed once the shell layer is migrated off $WORKTREE_PROJECT.
        name = os.environ.get("WORKTREE_PROJECT", "").strip()
    if not name:
        raise RuntimeError(
            "No active project could be resolved. agent-worktrees discovers its "
            "context from the current directory (like git); run from inside a "
            "managed repo or worktree, or pass --project <name>."
        )
    if not _PROJECT_NAME_RE.match(name):
        raise ValueError(
            f"Invalid project name: {name!r}. "
            "Must be 1-64 alphanumeric/dash/dot/underscore characters."
        )
    return name


def install_dir() -> Path:
    """Shared runtime root (``~/.agent-worktrees/``)."""
    return _home() / ".agent-worktrees"


def project_dir(name: str | None = None) -> Path:
    """Per-project config/state root (``~/.{name}/``)."""
    return _home() / f".{name or project_name()}"


def default_config_path() -> Path:
    """Return the machine-local config path for the active project."""
    return project_dir() / "config.yaml"


def global_config_path() -> Path:
    """Return the global, machine-wide config path (lowest config tier)."""
    return install_dir() / GLOBAL_CONFIG_FILENAME


def inrepo_config_path(anchor: str | Path) -> Path:
    """Return the preferred in-repo config path (directory form) for an anchor."""
    return Path(anchor) / INREPO_CONFIG_DIRNAME / GLOBAL_CONFIG_FILENAME


def load_config(path: Path | None = None) -> Config:
    """Load and parse the layered project config.

    Merges three tiers (highest precedence wins):

    1. ``~/.<project>/config.yaml`` (machine-local; ``path``) -- per-machine,
       per-repo overrides and machine paths. **Optional**: a repo designed for
       this system carries its settings in-repo and needs no machine-local
       file; machine-local config is the adapter that makes *foreign* repos
       compatible.
    2. ``<anchor>/.agent-worktrees/config.yaml`` (in-repo; the repo's own
       committed config -- the base for its settings).
    3. ``~/.agent-worktrees/config.yaml`` (global; machine-wide defaults).

    Top-level fields (``srcroot``/``machine``/``platform``/``copilot_profiles``
    /``headless``/``auto_fast_forward``) resolve machine-local > global >
    detected. Per-repo settings merge in-repo flat settings < machine-local
    ``repos.<name>`` block (the global tier carries no per-repo settings).
    Anchors come from the machine-local file or, when absent, from
    ``~/.agent-worktrees/repos.yaml``.

    Args:
        path: Machine-local config path. Uses the default if None.

    Returns:
        Parsed Config object.

    Raises:
        ValueError: If no repo can be resolved (no machine-local repos and no
            registry anchor for the active project).
    """
    if path is None:
        path = default_config_path()

    # Tier 1 (lowest): global machine-wide defaults.
    global_raw = _load_yaml_safe(global_config_path())
    # Tier 3 (highest): machine-local. Optional -- absent is fine. Service
    # config-drop-ins (``<config-dir>/config.d/*.yaml``) form a base UNDER the
    # machine-local ``config.yaml`` (so an explicit config.yaml still wins),
    # letting a service register machine-local settings -- e.g. the vault
    # contributing ``session_env.SUDO_ASKPASS`` -- without editing the shared
    # config.yaml. Drop-ins deep-merge with everything else, so a per-repo
    # ``session_env`` addition survives alongside the repo's own keys.
    machine_raw = _load_yaml_safe(path)
    dropins = _load_config_d(path.parent / "config.d")
    if dropins:
        machine_raw = _deep_merge(dropins, machine_raw)

    # Resolved top-level fields: machine-local > global > detected.
    platform = (
        machine_raw.get("platform")
        or global_raw.get("platform")
        or detect_platform()
    )
    machine = (
        machine_raw.get("machine")
        or global_raw.get("machine")
        or detect_machine()
    )
    srcroot = machine_raw.get("srcroot") or global_raw.get("srcroot") or ""

    # Active project / default repo name.
    repo_name = (
        machine_raw.get("repo_name")
        or global_raw.get("repo_name")
        or _project_name_safe()
    )

    machine_repos = machine_raw.get("repos") or {}
    if not isinstance(machine_repos, dict):
        machine_repos = {}

    # Build the set of repos to resolve: those named in the machine-local file,
    # plus the active project (so a convention-adopted repo with no
    # machine-local block still loads, with its anchor from the registry).
    names = [n for n in machine_repos if isinstance(machine_repos[n], dict)]
    if repo_name and repo_name not in names:
        names.append(repo_name)

    repos: dict[str, RepoConfig] = {}
    for name in names:
        machine_repo = machine_repos.get(name) or {}
        if not isinstance(machine_repo, dict):
            machine_repo = {}

        anchor = machine_repo.get("anchor") or _resolve_anchor_from_registry(
            name, platform
        )
        if not anchor:
            # No anchor anywhere -- can't manage this repo. Skip silently unless
            # it was the only candidate (validated after the loop).
            continue

        worktree_root = machine_repo.get("worktree_root") or derive_worktree_root(
            anchor
        )

        # Tier 2: the repo's own in-repo flat settings (base for repo settings).
        inrepo_settings = _load_inrepo_config(anchor)

        # Merge per-repo settings: in-repo base < machine-local override.
        # (The global tier carries only machine-wide top-level defaults --
        # srcroot/machine/platform/profiles -- never per-repo settings.)
        merged = _deep_merge(inrepo_settings, machine_repo)

        repos[name] = _build_repo_config(merged, anchor, str(worktree_root))

    if not repos:
        raise ValueError(
            f"No repo could be resolved for project {repo_name or '?'!r}.\n"
            f"Checked machine-local config ({path}) and the repos registry "
            f"({install_dir() / 'repos.yaml'}).\n"
            "Run the installer / register the repo first:\n"
            "  pwsh -File <repo>/plugins/agent-worktrees/scripts/install.ps1 install"
        )

    # copilot_profiles: machine-local if present, else global.
    profiles_raw = (
        machine_raw.get("copilot_profiles")
        if "copilot_profiles" in machine_raw
        else global_raw.get("copilot_profiles", [])
    )

    return Config(
        srcroot=srcroot,
        machine=machine,
        platform=platform,
        repo_name=repo_name,
        repos=repos,
        copilot_profiles=_parse_profiles(profiles_raw or []),
        headless=bool(
            machine_raw.get("headless", global_raw.get("headless", False))
        ),
        auto_fast_forward=bool(
            machine_raw.get(
                "auto_fast_forward", global_raw.get("auto_fast_forward", True)
            )
        ),
        new_picker=bool(
            machine_raw.get("new_picker", global_raw.get("new_picker", False))
        ),
    )


def _load_yaml_safe(path: Path) -> dict[str, Any]:
    """Load a YAML file into a dict, returning ``{}`` on any problem.

    Never raises: config loading is on the critical path of every command, so a
    missing, empty, or malformed file degrades to an empty mapping rather than
    breaking the whole CLI.
    """
    try:
        if not path.exists():
            return {}
        with open(path, encoding="utf-8") as f:
            raw = yaml.safe_load(f)
        return raw if isinstance(raw, dict) else {}
    except Exception:
        return {}


def _load_config_d(config_d: Path) -> dict[str, Any]:
    """Deep-merge every ``*.yaml`` in a ``config.d`` directory (sorted by name).

    A drop-in lets a service register machine-local config without editing the
    shared ``config.yaml`` (e.g. the vault registering
    ``session_env.SUDO_ASKPASS``). Files merge in lexical order (later names win
    among drop-ins); the caller layers the result UNDER ``config.yaml``. Never
    raises -- a missing dir or a bad file degrades to what parsed cleanly.
    """
    try:
        if not config_d.is_dir():
            return {}
        merged: dict[str, Any] = {}
        for f in sorted(config_d.glob("*.yaml")):
            merged = _deep_merge(merged, _load_yaml_safe(f))
        return merged
    except Exception:
        return {}


def _deep_merge(base: dict[str, Any], override: dict[str, Any]) -> dict[str, Any]:
    """Return ``base`` deep-merged with ``override`` (override wins).

    Nested dicts merge recursively; every other value (scalars, lists) is
    replaced wholesale by ``override``. Inputs are not mutated.
    """
    result: dict[str, Any] = dict(base)
    for key, ov in override.items():
        bv = result.get(key)
        if isinstance(bv, dict) and isinstance(ov, dict):
            result[key] = _deep_merge(bv, ov)
        else:
            result[key] = ov
    return result


def _project_name_safe() -> str:
    """Return the active project name, or ``""`` if none is resolved.

    Unlike :func:`project_name`, never raises -- used where an absent project is
    a tolerable condition (e.g. tests that pass an explicit config).
    """
    try:
        return project_name()
    except Exception:
        return ""


def _resolve_anchor_from_registry(name: str, platform: str) -> str | None:
    """Return the anchor path for ``name`` from ``repos.yaml``, or None.

    Lets a convention-adopted repo load with no machine-local config: the
    machine-specific path comes from the registry, the settings from the
    repo's own in-repo config. Never raises.
    """
    try:
        from . import repos as repos_mod

        registry = repos_mod.read_registry()
        entry = registry.repos.get(name)
        if entry is None:
            return None
        return entry.local_path(platform) or None
    except Exception:
        return None


def _build_repo_config(
    data: dict[str, Any], anchor: str, worktree_root: str
) -> RepoConfig:
    """Build a RepoConfig from a merged repo-settings dict + resolved paths.

    ``data`` is the per-repo settings after the three-tier merge; ``anchor``
    and ``worktree_root`` are the machine paths (resolved separately, since
    they never come from the shared in-repo config).
    """
    launch: dict[str, list[str]] = {}
    for plat_key, cmd_list in (data.get("launch") or {}).items():
        if isinstance(cmd_list, list):
            launch[plat_key] = [str(c) for c in cmd_list]

    launch_recovery: dict[str, list[str]] = {}
    for plat_key, cmd_list in (data.get("launch_recovery") or {}).items():
        if isinstance(cmd_list, list):
            launch_recovery[plat_key] = [str(c) for c in cmd_list]

    setup_hook: dict[str, str] = {}
    for plat_key, hook_path in (data.get("setup_hook") or {}).items():
        if isinstance(hook_path, str) and hook_path.strip():
            setup_hook[plat_key] = hook_path.strip()

    session_path: dict[str, list[str]] = {}
    for plat_key, dir_list in (data.get("session_path") or {}).items():
        if isinstance(dir_list, list):
            session_path[plat_key] = [str(d) for d in dir_list]

    session_env: dict[str, str] = {}
    for env_key, env_val in (data.get("session_env") or {}).items():
        if isinstance(env_key, str) and env_key.strip():
            session_env[env_key.strip()] = str(env_val)

    raw_vpaths = data.get("validate_paths", [])
    validate_paths = (
        [str(p) for p in raw_vpaths] if isinstance(raw_vpaths, list) else []
    )

    validate_hook: dict[str, list[str]] = {}
    for plat_key, cmd_list in (data.get("validate_hook") or {}).items():
        if isinstance(cmd_list, list):
            validate_hook[plat_key] = [str(c) for c in cmd_list]

    raw_spaths = data.get("service_paths", [])
    service_paths = (
        [str(p) for p in raw_spaths] if isinstance(raw_spaths, list) else []
    )

    post_install_hook: dict[str, list[str]] = {}
    for plat_key, cmd_list in (data.get("post_install_hook") or {}).items():
        if isinstance(cmd_list, list):
            post_install_hook[plat_key] = [str(c) for c in cmd_list]

    return RepoConfig(
        anchor=str(anchor),
        worktree_root=str(worktree_root or derive_worktree_root(anchor)),
        default_branch=data.get("default_branch", "master"),
        remote=data.get("remote", "origin"),
        launch=launch,
        launch_recovery=launch_recovery,
        setup_hook=setup_hook,
        session_path=session_path,
        session_env=session_env,
        validate_paths=validate_paths,
        validate_hook=validate_hook,
        service_paths=service_paths,
        post_install_hook=post_install_hook,
        pr=_parse_pr(data.get("pr")),
        base_repo=bool(data.get("base_repo", False)),
    )


def _load_inrepo_config(anchor: str) -> dict[str, Any]:
    """Return the repo's in-repo flat settings dict, or ``{}``.

    Reads ``<anchor>/.agent-worktrees/config.yaml`` (preferred, directory form).
    Falls back to the legacy single-file ``<anchor>/.agent-worktrees.yaml``
    (which carried only a ``pr:`` block -- still a valid, minimal flat
    settings dict). Never raises: a missing or malformed file degrades to an
    empty mapping so config loading cannot be broken by a bad committed file.
    """
    dir_form = _load_yaml_safe(inrepo_config_path(anchor))
    if dir_form:
        return dir_form
    return _load_yaml_safe(Path(anchor) / INREPO_CONFIG_FILENAME)


def _parse_pr(raw: Any) -> PRConfig:
    """Parse the optional ``pr:`` block of a repo config into a PRConfig.

    Unknown or missing values fall back to PRConfig defaults (disabled).
    """
    if not isinstance(raw, dict):
        return PRConfig()
    required = bool(raw.get("required", False))
    # ``required`` implies ``enabled``: enforcing PRs only makes sense when
    # PR mode is on, so a lone ``required: true`` turns the mode on too.
    enabled = bool(raw.get("enabled", False)) or required
    raw_labels = raw.get("labels", ())
    labels: tuple[str, ...]
    if isinstance(raw_labels, (list, tuple)):
        labels = tuple(str(x) for x in raw_labels)
    elif raw_labels:
        labels = (str(raw_labels),)
    else:
        labels = ()
    head_scheme = str(raw.get("head_scheme", "snapshot")).strip().lower()
    if head_scheme not in ("snapshot", "refspec"):
        head_scheme = "snapshot"
    return PRConfig(
        enabled=enabled,
        required=required,
        provider=str(raw.get("provider", "gitea")),
        strategy=str(raw.get("strategy", "detach")),
        branch_prefix=str(raw.get("branch_prefix", "feature")),
        head_scheme=head_scheme,
        head_pattern=str(raw.get("head_pattern", "")),
        api_base=str(raw.get("api_base", "")),
        token_env=str(raw.get("token_env", "")),
        token_command=str(raw.get("token_command", "")),
        labels=labels,
        auto_open=bool(raw.get("auto_open", False)),
    )


def _parse_profiles(raw_list: list[Any]) -> list[CopilotProfile]:
    """Parse and validate copilot_profiles from config YAML."""
    if not isinstance(raw_list, list):
        return []

    profiles: list[CopilotProfile] = []
    seen_names: set[str] = set()

    for entry in raw_list:
        if not isinstance(entry, dict):
            continue
        name = entry.get("name", "")
        if not name or name in seen_names:
            continue
        seen_names.add(name)

        env: dict[str, str] = {}
        raw_env = entry.get("env", {})
        if isinstance(raw_env, dict):
            for k, v in raw_env.items():
                if _ENV_KEY_RE.match(str(k)):
                    env[str(k)] = str(v)

        raw_args = entry.get("copilot_args", [])
        copilot_args = [str(a) for a in raw_args] if isinstance(raw_args, list) else []

        profiles.append(CopilotProfile(
            name=name,
            label=entry.get("label", name),
            env=env,
            copilot_args=copilot_args,
        ))

    return profiles


def derive_worktree_root(anchor: str | Path) -> str:
    """Default worktree root for an *anchor*: a sibling ``<anchor>.worktrees``
    directory.

    This mirrors the GitHub Copilot CLI's native ``--worktree`` / ``/worktree``
    layout (worktrees created as a ``<repo>.worktrees`` sibling of the repo),
    so worktrees created by agent-worktrees and by Copilot land in the same
    place and are mutually discoverable.  Used whenever a repo's config omits
    an explicit ``worktree_root`` (the field remains an optional override)."""
    return f"{str(anchor).rstrip('/').rstrip(chr(92))}.worktrees"


def tracking_dir() -> Path:
    """Return the worktree tracking directory path (per-project)."""
    return project_dir() / "worktrees"


def venv_python() -> Path:
    """Return the path to the venv's Python interpreter (shared runtime)."""
    base = install_dir() / ".venv"
    if platform.system() == "Windows":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"
