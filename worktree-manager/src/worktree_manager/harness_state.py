"""Harness state read-model (Phase 3 — the Manager's introspection backbone).

The Manager surfaces the *real* configuration state of the harness. It reads that
state from the files the harness already writes for its OWN reasons — never by
importing plugin code — so the dependency-free boundary holds:

* **user-global Copilot settings** — ``~/.copilot/settings.json`` (``enabledPlugins``
  keyed ``<plugin>@<marketplace>``, ``extraKnownMarketplaces``).
* **repos registry** — ``~/.agent-worktrees/repos.yaml`` (class = worktree mode,
  ``agent`` mode, remote, per-platform checkout path, tags, ``account_map``).
* **projects registry** — ``~/.agent-worktrees/projects.yaml`` (which repos are
  *projects* — harness repos worthy of binstubs + profiles — with ``config_dir``,
  ``expose_agent``, wsl).
* **per-project machine roster** — ``<repo>/.agent-worktrees/machines.yaml``
  (legacy fallback: ``<repo>/machines.yaml``) — the host x SSH-environment
  roster a project's terminal-profile fragment is built against.
* **per-project harness config** — ``~/.<project>/config.yaml`` (``knowledge_repo``
  link, ``terminal_profiles``).
* **per-project enabled plugins** — the project checkout's own
  ``.github/copilot/settings.json`` ``enabledPlugins``.

This module is read-only; mutation (adoption, linking, config edits) lives
elsewhere. Everything degrades gracefully when a file is absent.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from pathlib import Path

import yaml

SETTINGS_RELS: tuple[tuple[str, ...], ...] = (
    (".claude", "settings.json"),
    (".claude", "settings.local.json"),
    (".github", "copilot", "settings.json"),
    (".github", "copilot", "settings.local.json"),
)


def home() -> Path:
    return Path(os.environ.get("USERPROFILE") or os.path.expanduser("~"))


def _platform_key() -> str:
    if os.name == "nt":
        return "windows"
    import platform as _p
    return "macos" if _p.system() == "Darwin" else "linux"


def _read_json(p: Path) -> dict:
    try:
        return json.loads(p.read_text("utf-8"))
    except (OSError, ValueError):
        return {}


def _read_yaml(p: Path) -> dict:
    try:
        data = yaml.safe_load(p.read_text("utf-8"))
        return data if isinstance(data, dict) else {}
    except (OSError, yaml.YAMLError):
        return {}


# ── plugins (user-global enablement) ────────────────────────────────────────

@dataclass(frozen=True)
class EnabledPlugin:
    """One entry from ``enabledPlugins`` — ``<name>@<marketplace>: bool``."""

    name: str
    marketplace: str
    enabled: bool

    @property
    def qualified(self) -> str:
        return f"{self.name}@{self.marketplace}"


def user_settings(home_dir: Path | None = None) -> dict:
    return _read_json((home_dir or home()) / ".copilot" / "settings.json")


def _parse_enabled(settings: dict) -> list[EnabledPlugin]:
    out = []
    for key, val in (settings.get("enabledPlugins") or {}).items():
        name, _, market = str(key).partition("@")
        out.append(EnabledPlugin(name=name, marketplace=market or "?", enabled=bool(val)))
    return out


def user_enabled_plugins(home_dir: Path | None = None) -> list[EnabledPlugin]:
    return _parse_enabled(user_settings(home_dir))


# ── repos + projects ────────────────────────────────────────────────────────

@dataclass(frozen=True)
class RepoInfo:
    """A known repo (repos.yaml) with its config-state indicators."""

    name: str
    klass: str                 # "worktree" | "singleton" | "reference"
    agent: bool                # agent-guarded? (agent mode)
    remote: str | None
    path: str | None
    account: str | None        # ownership (explicit or account_map-derived)
    tags: tuple[str, ...] = ()
    is_project: bool = False   # promoted to a project (binstubs + profiles)?
    pr_model: str = "?"        # "pr-required" | "pr" | "direct" | "?"

    @property
    def worktree_mode(self) -> str:
        return self.klass


@dataclass(frozen=True)
class SshEnvironment:
    """One ``ssh.environments`` entry from a project's ``machines.yaml``."""

    name: str            # windows | wsl | linux
    alias: str
    shell: str = ""      # pwsh | bash


@dataclass(frozen=True)
class RosterMachine:
    """One machine from a project's ``machines.yaml`` roster."""

    key: str
    display_name: str
    ssh_ready: bool = False
    hostname: str | None = None
    environments: tuple[SshEnvironment, ...] = ()

    def identities(self) -> set[str]:
        """Lower-cased ids used to recognise the local host (self-skip)."""
        ids = {self.key}
        if self.display_name:
            ids.add(self.display_name)
        if self.hostname:
            ids.add(self.hostname)
        for e in self.environments:
            if e.alias:
                ids.add(e.alias)
        return {i.lower() for i in ids if i}


@dataclass(frozen=True)
class ProjectInfo:
    """A registered project (projects.yaml) joined with its repo + config."""

    name: str
    config_dir: str | None
    expose_agent: bool
    knowledge_repo: str | None
    profiles: int
    repo: RepoInfo | None
    enabled_plugins: tuple[str, ...] = field(default=())
    wsl_distro: str | None = None
    wsl_state: str | None = None
    roster: tuple[RosterMachine, ...] = field(default=())
    display_name: str | None = None
    anchor: str | None = None
    """This project's resolved checkout path: ``repo.path`` (repos.yaml) when
    a matching repos.yaml entry exists, else this project's own
    ``projects.yaml``-level ``anchor:`` override -- a real, actively-used
    field for a project registered without (or overriding) a repos.yaml
    entry (ported from ``agent_worktrees.terminal_fragment.anchor_for``,
    copilot-extensions#3390 Phase 3e Step 3b)."""


def repos_registry(home_dir: Path | None = None) -> dict:
    return _read_yaml((home_dir or home()) / ".agent-worktrees" / "repos.yaml")


def projects_registry(home_dir: Path | None = None) -> dict:
    return _read_yaml((home_dir or home()) / ".agent-worktrees" / "projects.yaml")


def project_config(name: str, home_dir: Path | None = None) -> dict:
    return _read_yaml((home_dir or home()) / f".{name}" / "config.yaml")


def _load_roster(machines_yaml: Path) -> tuple[RosterMachine, ...]:
    """Parse a ``machines.yaml`` file into :class:`RosterMachine` rows.

    Ported verbatim from ``agent_worktrees.terminal_fragment._load_roster``
    (copilot-extensions#3390, Phase 3e Step 2) -- same tolerant, degrade-to-
    empty behavior on a missing/malformed file.
    """
    if not machines_yaml.exists():
        return ()
    try:
        data = yaml.safe_load(machines_yaml.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return ()
    machines = data.get("machines") if isinstance(data, dict) else None
    if not isinstance(machines, dict):
        return ()
    out: list[RosterMachine] = []
    for key, entry in machines.items():
        if not isinstance(entry, dict):
            continue
        ssh = entry.get("ssh") if isinstance(entry.get("ssh"), dict) else {}
        envs = []
        for e in (ssh.get("environments") or []):
            if not isinstance(e, dict):
                continue
            name = str(e.get("name") or "").strip()
            if not name:
                continue
            envs.append(SshEnvironment(
                name=name,
                alias=str(e.get("alias") or "").strip(),
                shell=str(e.get("shell") or "").strip(),
            ))
        out.append(RosterMachine(
            key=str(key),
            display_name=str(entry.get("display_name") or key),
            ssh_ready=bool(ssh.get("ready")),
            hostname=(str(entry.get("hostname")) if entry.get("hostname") else None),
            environments=tuple(envs),
        ))
    return tuple(out)


def project_roster(repo_path: str | None) -> tuple[RosterMachine, ...]:
    """This project's ``machines.yaml`` roster, given its resolved repo path.

    Checks ``<repo_path>/.agent-worktrees/machines.yaml`` first, falling back
    to the legacy repo-root ``<repo_path>/machines.yaml`` -- matching
    ``terminal_fragment.collect_local_projects``'s existing fallback order.
    """
    if not repo_path:
        return ()
    root = Path(repo_path)
    machines_yaml = root / ".agent-worktrees" / "machines.yaml"
    if not machines_yaml.is_file():
        machines_yaml = root / "machines.yaml"
    return _load_roster(machines_yaml)


def _account_for(name: str, entry: dict, account_map: dict) -> str | None:
    if entry.get("account"):
        return str(entry["account"])
    remote = entry.get("remote") or ""
    # host/owner/... — pull the owner segment and map it.
    for owner, login in account_map.items():
        if f"/{owner}/" in remote or remote.endswith(f"/{owner}"):
            return f"{login} (derived)"
    return None


def pr_model(repo_path: str | None) -> str:
    if not repo_path:
        return "?"
    cfg = Path(repo_path) / ".agent-worktrees" / "config.yaml"
    data = _read_yaml(cfg)
    pr = data.get("pr") if isinstance(data.get("pr"), dict) else {}
    if pr.get("required"):
        return "pr-required"
    if pr.get("enabled"):
        return "pr"
    if cfg.is_file():
        return "direct"
    return "?"


def repo_plugin_enablement(repo_path: str | None) -> dict[str, bool]:
    """Merged repo plugin settings with native/local last-file-wins precedence."""
    if not repo_path:
        return {}
    enabled: dict[str, bool] = {}
    root = Path(repo_path)
    for rel in SETTINGS_RELS:
        settings = _read_json(root.joinpath(*rel))
        values = settings.get("enabledPlugins")
        if not isinstance(values, dict):
            continue
        for name, value in values.items():
            if isinstance(name, str) and isinstance(value, bool):
                enabled[name] = value
    return enabled


def repo_enabled_plugins(repo_path: str | None) -> list[str]:
    return [
        name
        for name, enabled in repo_plugin_enablement(repo_path).items()
        if enabled
    ]


def build_repos(home_dir: Path | None = None) -> list[RepoInfo]:
    reg = repos_registry(home_dir)
    projects = set((projects_registry(home_dir).get("projects") or {}).keys())
    account_map = reg.get("account_map") or {}
    pkey = _platform_key()
    out: list[RepoInfo] = []
    for name, entry in (reg.get("repos") or {}).items():
        entry = entry or {}
        path = entry.get(pkey) or entry.get("windows") or entry.get("linux") or entry.get("wsl")
        out.append(RepoInfo(
            name=name,
            klass=entry.get("class", "?"),
            agent=bool(entry.get("agent", True)),
            remote=entry.get("remote"),
            path=path,
            account=_account_for(name, entry, account_map),
            tags=tuple(entry.get("tags") or ()),
            is_project=name in projects,
            pr_model=pr_model(path),
        ))
    return sorted(out, key=lambda r: (not r.is_project, r.name))


def build_projects(home_dir: Path | None = None) -> list[ProjectInfo]:
    reg = projects_registry(home_dir)
    repos = {r.name: r for r in build_repos(home_dir)}
    out: list[ProjectInfo] = []
    for name, entry in (reg.get("projects") or {}).items():
        entry = entry or {}
        cfg = project_config(name, home_dir)
        repo = repos.get(name)
        wsl = entry.get("wsl") if isinstance(entry.get("wsl"), dict) else {}
        # Prefer the repos.yaml-resolved path; fall back to this project's
        # own projects.yaml `anchor:` override for a project registered
        # without (or overriding) a repos.yaml entry.
        anchor_override = entry.get("anchor") if isinstance(entry.get("anchor"), str) else None
        anchor = (repo.path if repo else None) or anchor_override
        out.append(ProjectInfo(
            name=name,
            config_dir=entry.get("config_dir"),
            expose_agent=bool(entry.get("expose_agent", False)),
            knowledge_repo=cfg.get("knowledge_repo"),
            profiles=len(cfg.get("terminal_profiles") or []),
            repo=repo,
            enabled_plugins=tuple(repo_enabled_plugins(repo.path) if repo else ()),
            wsl_distro=wsl.get("distro"),
            wsl_state=wsl.get("state"),
            roster=project_roster(anchor),
            display_name=entry.get("display_name"),
            anchor=anchor,
        ))
    return sorted(out, key=lambda p: p.name)


@dataclass(frozen=True)
class HarnessState:
    user_enabled: tuple[EnabledPlugin, ...]
    projects: tuple[ProjectInfo, ...]
    repos: tuple[RepoInfo, ...]

    def enabled_names(self) -> set[str]:
        return {e.name for e in self.user_enabled if e.enabled}


def build_state(home_dir: Path | None = None) -> HarnessState:
    return HarnessState(
        user_enabled=tuple(user_enabled_plugins(home_dir)),
        projects=tuple(build_projects(home_dir)),
        repos=tuple(build_repos(home_dir)),
    )
