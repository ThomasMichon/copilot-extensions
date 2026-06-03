"""Config loading and machine detection.

Reads per-project config from ~/.{project}/config.yaml and provides
typed access.  Runtime lives at ~/.agent-worktrees/ (shared across
projects); per-project state at ~/.{project}/.

The active project is determined by $WORKTREE_PROJECT (required).
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
class RepoConfig:
    """Configuration for a single managed repository."""

    anchor: str
    worktree_root: str
    default_branch: str = "master"
    remote: str = "origin"
    launch: dict[str, list[str]] = field(default_factory=dict)
    launch_recovery: dict[str, list[str]] = field(default_factory=dict)
    validate_paths: list[str] = field(default_factory=list)
    validate_hook: dict[str, list[str]] = field(default_factory=dict)
    service_paths: list[str] = field(default_factory=list)
    post_install_hook: dict[str, list[str]] = field(default_factory=dict)


@dataclass(frozen=True)
class Config:
    """Top-level project configuration."""

    srcroot: str
    machine: str
    platform: str
    repo_name: str = ""
    repos: dict[str, RepoConfig] = field(default_factory=dict)
    copilot_profiles: list[CopilotProfile] = field(default_factory=list)

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
    """Look up a machine by key or alias.

    Checks exact key match first, then scans aliases.  Returns None
    if no entry matches.
    """
    if name in entries:
        return entries[name]
    for entry in entries.values():
        if entry.alias and entry.alias.lower() == name.lower():
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
            # Exact match on key (real hostname) first
            for key, entry in entries.items():
                if hostname == key:
                    return machine_name(entry)
            # Then check aliases
            for key, entry in entries.items():
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


def project_name() -> str:
    """Active project name from ``$WORKTREE_PROJECT``.

    Raises ``RuntimeError`` if ``$WORKTREE_PROJECT`` is not set.
    """
    name = os.environ.get("WORKTREE_PROJECT", "").strip()
    if not name:
        raise RuntimeError(
            "WORKTREE_PROJECT environment variable is required but not set. "
            "Set it to your project name (e.g. 'my-project', 'dotfiles')."
        )
    if not _PROJECT_NAME_RE.match(name):
        raise ValueError(
            f"Invalid WORKTREE_PROJECT value: {name!r}. "
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
    """Return the config path for the active project."""
    return project_dir() / "config.yaml"


def load_config(path: Path | None = None) -> Config:
    """Load and parse the project config YAML.

    Args:
        path: Path to config.yaml. Uses default if None.

    Returns:
        Parsed Config object.

    Raises:
        FileNotFoundError: If config file doesn't exist.
        ValueError: If config is malformed.
    """
    if path is None:
        path = default_config_path()

    if not path.exists():
        raise FileNotFoundError(
            f"No config found at {path}.\n"
            "Run the installer first:\n"
            "  pwsh -File <repo>/plugins/agent-worktrees/scripts/install.ps1 install"
        )

    with open(path, encoding="utf-8") as f:
        raw: dict[str, Any] = yaml.safe_load(f)

    if not raw:
        raise ValueError(f"Config at {path} is empty")

    repos: dict[str, RepoConfig] = {}
    for name, repo_data in raw.get("repos", {}).items():
        if isinstance(repo_data, dict):
            # Parse launch commands (optional)
            launch: dict[str, list[str]] = {}
            launch_recovery: dict[str, list[str]] = {}
            for plat_key, cmd_list in repo_data.get("launch", {}).items():
                if isinstance(cmd_list, list):
                    launch[plat_key] = [str(c) for c in cmd_list]
            for plat_key, cmd_list in repo_data.get("launch_recovery", {}).items():
                if isinstance(cmd_list, list):
                    launch_recovery[plat_key] = [str(c) for c in cmd_list]

            # Parse validate_paths (optional list of repo-relative dirs)
            raw_vpaths = repo_data.get("validate_paths", [])
            validate_paths = (
                [str(p) for p in raw_vpaths] if isinstance(raw_vpaths, list) else []
            )

            # Parse validate_hook (optional platform-keyed command lists)
            validate_hook: dict[str, list[str]] = {}
            for plat_key, cmd_list in repo_data.get("validate_hook", {}).items():
                if isinstance(cmd_list, list):
                    validate_hook[plat_key] = [str(c) for c in cmd_list]

            # Parse service_paths (optional list of glob patterns)
            raw_spaths = repo_data.get("service_paths", [])
            service_paths = (
                [str(p) for p in raw_spaths] if isinstance(raw_spaths, list) else []
            )

            # Parse post_install_hook (optional platform-keyed command lists)
            post_install_hook: dict[str, list[str]] = {}
            raw_pih = repo_data.get("post_install_hook", {})
            if isinstance(raw_pih, dict):
                for plat_key, cmd_list in raw_pih.items():
                    if isinstance(cmd_list, list):
                        post_install_hook[plat_key] = [str(c) for c in cmd_list]

            repos[name] = RepoConfig(
                anchor=repo_data["anchor"],
                worktree_root=repo_data["worktree_root"],
                default_branch=repo_data.get("default_branch", "master"),
                remote=repo_data.get("remote", "origin"),
                launch=launch,
                launch_recovery=launch_recovery,
                validate_paths=validate_paths,
                validate_hook=validate_hook,
                service_paths=service_paths,
                post_install_hook=post_install_hook,
            )

    repo_name = raw.get("repo_name")
    if not repo_name:
        repo_name = project_name()

    return Config(
        srcroot=raw.get("srcroot", ""),
        machine=raw.get("machine", detect_machine()),
        platform=raw.get("platform", detect_platform()),
        repo_name=repo_name,
        repos=repos,
        copilot_profiles=_parse_profiles(raw.get("copilot_profiles", [])),
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


def tracking_dir() -> Path:
    """Return the worktree tracking directory path (per-project)."""
    return project_dir() / "worktrees"


def venv_python() -> Path:
    """Return the path to the venv's Python interpreter (shared runtime)."""
    base = install_dir() / ".venv"
    if platform.system() == "Windows":
        return base / "Scripts" / "python.exe"
    return base / "bin" / "python"
