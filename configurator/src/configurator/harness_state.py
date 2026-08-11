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
class ProjectInfo:
    """A registered project (projects.yaml) joined with its repo + config."""

    name: str
    config_dir: str | None
    expose_agent: bool
    knowledge_repo: str | None
    profiles: int
    repo: RepoInfo | None
    enabled_plugins: tuple[str, ...] = field(default=())


def repos_registry(home_dir: Path | None = None) -> dict:
    return _read_yaml((home_dir or home()) / ".agent-worktrees" / "repos.yaml")


def projects_registry(home_dir: Path | None = None) -> dict:
    return _read_yaml((home_dir or home()) / ".agent-worktrees" / "projects.yaml")


def project_config(name: str, home_dir: Path | None = None) -> dict:
    return _read_yaml((home_dir or home()) / f".{name}" / "config.yaml")


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


def repo_enabled_plugins(repo_path: str | None) -> list[str]:
    if not repo_path:
        return []
    settings = _read_json(Path(repo_path) / ".github" / "copilot" / "settings.json")
    return list((settings.get("enabledPlugins") or {}).keys())


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
        out.append(ProjectInfo(
            name=name,
            config_dir=entry.get("config_dir"),
            expose_agent=bool(entry.get("expose_agent", False)),
            knowledge_repo=cfg.get("knowledge_repo"),
            profiles=len(cfg.get("terminal_profiles") or []),
            repo=repo,
            enabled_plugins=tuple(repo_enabled_plugins(repo.path) if repo else ()),
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
