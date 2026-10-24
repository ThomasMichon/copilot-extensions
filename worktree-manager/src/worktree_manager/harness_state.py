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
import re
import stat
import subprocess
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


def _has_dangling_link(path: Path) -> bool:
    for candidate in (path, *path.parents):
        try:
            info = candidate.lstat()
        except FileNotFoundError:
            continue
        linked = stat.S_ISLNK(info.st_mode) or (getattr(info, "st_file_attributes", 0) or 0) & 0x400
        if linked and not candidate.exists():
            return True
    return False


class _StrictYamlLoader(yaml.SafeLoader):
    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise ValueError("YAML mapping node is invalid")
        self.flatten_mapping(node)
        result = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node, deep=deep)
            try:
                duplicate = key in result
            except TypeError as error:
                raise ValueError("YAML mapping keys must be scalar values") from error
            if duplicate:
                raise ValueError("Duplicate YAML mapping key")
            result[key] = self.construct_object(value_node, deep=deep)
        return result


def _load_yaml(text: str, *, strict: bool = False):
    return yaml.load(text, Loader=_StrictYamlLoader) if strict else yaml.safe_load(text)


def _read_yaml(p: Path, *, strict: bool = False, required_key: str | None = None) -> dict:
    try:
        data = _load_yaml(p.read_text("utf-8"), strict=strict)
        if strict and data is not None and not isinstance(data, dict):
            raise ValueError(f"Configuration must be a mapping: {p}")
        if strict and required_key is not None and (
            not isinstance(data, dict) or required_key not in data
        ):
            raise ValueError(f"Registry requires an explicit {required_key} mapping: {p}")
        return data if isinstance(data, dict) else {}
    except FileNotFoundError as error:
        if strict and _has_dangling_link(p):
            raise ValueError(f"Cannot read configuration mapping: {p}") from error
        return {}
    except (OSError, yaml.YAMLError) as error:
        if strict:
            raise ValueError(f"Cannot read configuration mapping: {p}") from error
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


@dataclass(frozen=True)
class ProfileInputRoots:
    """Caller-resolved input roots; never change the machine-global outputs."""

    registry: Path
    projects: dict[str, Path]

    def project_dir(self, name: str) -> Path:
        if name not in self.projects:
            raise ValueError(f"Selected profile inputs omit project {name}")
        return self.projects[name]


def _registry_path(home_dir, kind, *, input_roots=None) -> Path:
    root = input_roots.registry if input_roots else (home_dir or home()) / ".agent-worktrees"
    return root / f"{kind}.yaml"


def _registry(
    home_dir: Path | None, kind: str, *, strict: bool,
    input_roots: ProfileInputRoots | None = None,
) -> dict:
    path = _registry_path(home_dir, kind, input_roots=input_roots)
    data = _read_yaml(path, strict=True, required_key=kind) if strict else _read_yaml(path)
    if strict:
        entries = data.get(kind, {})
        if not isinstance(entries, dict) or any(
            not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9_.-]{1,64}", name)
            or name in {".", ".."} or (entry is not None and not isinstance(entry, dict))
            for name, entry in entries.items()
        ):
            raise ValueError(f"{kind.title()} registry must contain a {kind} mapping with valid entries")
        if kind == "repos" and not isinstance(data.get("account_map", {}), dict):
            raise ValueError("Repos registry account_map must be a mapping")
        for name, entry in entries.items():
            if entry is not None:
                _validate_registry_entry(kind, name, entry)
    return data


def _validate_registry_entry(kind: str, name: str, entry: dict) -> None:
    string_fields = (
        ("windows", "linux", "wsl", "remote", "class", "account")
        if kind == "repos" else ("config_dir", "anchor", "display_name")
    )
    for field_name in string_fields:
        value = entry.get(field_name)
        if value is not None and not isinstance(value, str):
            raise ValueError(f"{kind}.{name}.{field_name} must be a string")
    if kind == "repos":
        tags = entry.get("tags")
        if tags is not None and (
            not isinstance(tags, list) or any(not isinstance(tag, str) for tag in tags)
        ):
            raise ValueError(f"repos.{name}.tags must be a list of strings")
    else:
        wsl = entry.get("wsl")
        if wsl is not None and (
            not isinstance(wsl, dict) or any(
                wsl.get(key) is not None and not isinstance(wsl[key], str)
                for key in ("distro", "state")
            )
        ):
            raise ValueError(f"projects.{name}.wsl must contain string distro/state values")


def repos_registry(
    home_dir: Path | None = None, *, strict: bool = False,
    input_roots: ProfileInputRoots | None = None,
) -> dict:
    return _registry(home_dir, "repos", strict=strict, input_roots=input_roots)


def projects_registry(
    home_dir: Path | None = None, *, strict: bool = False,
    input_roots: ProfileInputRoots | None = None,
) -> dict:
    return _registry(home_dir, "projects", strict=strict, input_roots=input_roots)


def project_config(name: str, home_dir: Path | None = None) -> dict:
    return _read_yaml((home_dir or home()) / f".{name}" / "config.yaml")


def _load_roster(machines_yaml: Path, *, strict: bool = False) -> tuple[RosterMachine, ...]:
    """Parse a ``machines.yaml`` file into :class:`RosterMachine` rows.

    Ported verbatim from ``agent_worktrees.terminal_fragment._load_roster``
    (copilot-extensions#3390, Phase 3e Step 2) -- same tolerant, degrade-to-
    empty behavior on a missing/malformed file.
    """
    if not machines_yaml.exists():
        if strict and _has_dangling_link(machines_yaml):
            raise ValueError(f"Cannot read machine roster: {machines_yaml}")
        return ()
    if strict and not machines_yaml.is_file():
        raise ValueError(f"Machine roster must be a file: {machines_yaml}")
    try:
        data = _load_yaml(machines_yaml.read_text(encoding="utf-8"), strict=strict) or {}
    except (OSError, yaml.YAMLError) as error:
        if strict:
            raise ValueError(f"Cannot read machine roster: {machines_yaml}") from error
        return ()
    machines = data.get("machines") if isinstance(data, dict) else None
    if not isinstance(machines, dict):
        if strict:
            raise ValueError(f"Machine roster must contain a machines mapping: {machines_yaml}")
        return ()
    out: list[RosterMachine] = []
    for key, entry in machines.items():
        if not isinstance(entry, dict):
            if strict:
                raise ValueError(f"Invalid machine entry in roster: {machines_yaml}")
            continue
        if strict and "ssh" in entry and not isinstance(entry["ssh"], dict):
            raise ValueError(f"Invalid SSH entry in roster: {machines_yaml}")
        ssh = entry.get("ssh") if isinstance(entry.get("ssh"), dict) else {}
        envs = []
        environments = ssh.get("environments") if strict else (ssh.get("environments") or [])
        if environments is None:
            environments = []
        if strict and not isinstance(environments, list):
            raise ValueError(f"Invalid SSH environments in roster: {machines_yaml}")
        for e in environments:
            if not isinstance(e, dict):
                if strict:
                    raise ValueError(f"Invalid SSH environment in roster: {machines_yaml}")
                continue
            name = str(e.get("name") or "").strip()
            if not name:
                if strict:
                    raise ValueError(f"Missing SSH environment name in roster: {machines_yaml}")
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


def project_roster(repo_path: str | None, *, strict: bool = False) -> tuple[RosterMachine, ...]:
    """This project's ``machines.yaml`` roster, given its resolved repo path.

    Checks ``<repo_path>/.agent-worktrees/machines.yaml`` first, falling back
    to the legacy repo-root ``<repo_path>/machines.yaml`` -- matching
    ``terminal_fragment.collect_local_projects``'s existing fallback order.
    """
    if not repo_path:
        return ()
    root = Path(repo_path)
    machines_yaml = root / ".agent-worktrees" / "machines.yaml"
    if strict:
        try:
            machines_yaml.lstat()
        except FileNotFoundError:
            if _has_dangling_link(machines_yaml):
                raise ValueError(f"Cannot read machine roster: {machines_yaml}")
            machines_yaml = root / "machines.yaml"
        return _load_roster(machines_yaml, strict=True)
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


def _registered_repo_path(entry, platform_key):
    return entry.get(platform_key) or entry.get("windows") or entry.get("linux") or entry.get("wsl")


def _selected_project_anchor(repo_path, entry):
    override = entry.get("anchor") if isinstance(entry.get("anchor"), str) else None
    return repo_path or override


@dataclass(frozen=True)
class ProjectAnchorSelection:
    anchor: str | None
    dependency_paths: tuple[Path, ...]


def project_anchor_selection(
    name: str, home_dir: Path | None = None, *,
    input_roots: ProfileInputRoots | None = None, reader=None,
) -> ProjectAnchorSelection:
    """Resolve only one registered project's anchor, without materializing it."""
    paths = tuple(
        _registry_path(home_dir, kind, input_roots=input_roots)
        for kind in ("repos", "projects")
    )
    read = reader if reader is not None else lambda path: _read_yaml(path, strict=True)
    documents = [read(path) for path in paths]
    registries = []
    for document, kind in zip(documents, ("repos", "projects")):
        if not isinstance(document, dict):
            raise ValueError(f"{kind} registry must be a mapping")
        entries = document.get(kind, {})
        if not isinstance(entries, dict):
            raise ValueError(f"{kind} entries must be a mapping")
        registries.append(entries)
    repos, projects = registries
    if name not in projects:
        return ProjectAnchorSelection(None, paths)
    project_entry, repo_entry = projects[name], repos.get(name)
    project_entry = {} if project_entry is None else project_entry
    repo_entry = {} if repo_entry is None else repo_entry
    if not isinstance(project_entry, dict) or not isinstance(repo_entry, dict):
        raise ValueError("Selected project and repository entries must be mappings")
    anchor = _selected_project_anchor(
        _registered_repo_path(repo_entry, _platform_key()), project_entry,
    )
    if anchor is not None and not isinstance(anchor, str):
        raise ValueError("Selected project anchor must be a string")
    return ProjectAnchorSelection(anchor, paths)


def build_repos(
    home_dir: Path | None = None, *, strict: bool = False,
    input_roots: ProfileInputRoots | None = None,
) -> list[RepoInfo]:
    kwargs = {"strict": strict, "input_roots": input_roots} if input_roots else (
        {"strict": True} if strict else {}
    )
    reg = repos_registry(home_dir, **kwargs)
    project_reg = projects_registry(home_dir, **kwargs)
    projects = set((project_reg.get("projects") or {}).keys())
    account_map = reg.get("account_map") or {}
    pkey = _platform_key()
    out: list[RepoInfo] = []
    for name, entry in (reg.get("repos") or {}).items():
        entry = entry or {}
        if strict and type(entry.get("agent", False)) is not bool:
            raise ValueError(f"Registered repository {name!r} agent must be a Boolean")
        path = _registered_repo_path(entry, pkey)
        out.append(RepoInfo(
            name=name,
            klass=entry.get("class", "?"),
            agent=entry.get("agent") is True,
            remote=entry.get("remote"),
            path=path,
            account=_account_for(name, entry, account_map),
            tags=tuple(entry.get("tags") or ()),
            is_project=name in projects,
            pr_model=pr_model(path),
        ))
    return sorted(out, key=lambda r: (not r.is_project, r.name))


def _exact_platform_key() -> str:
    """This platform's own ``repos.yaml`` key, with no cross-platform fallback.

    Unlike ``_platform_key()`` (used by ``build_repos()``'s deliberate
    cross-platform fallback chain, so a repo without its *own* machine's
    entry still resolves to whichever path is registered), this never falls
    back to another OS's entry and distinguishes WSL from plain Linux (the
    same ``/proc/version`` probe ``terminal_fragment.detect_platform()``
    uses) — ``mis_registered_repos()`` must only ever inspect the path a repo
    actually registered for *this exact* platform, never borrow a sibling
    platform's path and judge it as if it were this machine's own.

    ``repos.yaml`` itself only ever carries ``windows``/``wsl``/``linux``
    keys — agent-worktrees' own platform resolver maps macOS to ``linux``
    too (there is no ``macos`` key), so this never returns anything else.
    """
    if os.name == "nt":
        return "windows"
    try:
        with open("/proc/version") as f:
            if "microsoft" in f.read().lower():
                return "wsl"
    except OSError:
        pass
    return "linux"


def _is_real_git_checkout(path: Path) -> bool | None:
    """Probe git itself rather than trust `.git`/``HEAD``+``objects`` markers.

    A marker-only check (a bare, possibly empty ``.git`` directory; a
    ``HEAD``+``objects`` pair left over from a partial/corrupt bare clone)
    can pass a filesystem-only test while being unusable to git. Ask git
    directly whether ``path`` is a bare repository or is the exact top-level
    of a working-tree checkout, with ambient ``GIT_*`` overrides cleared so
    an unrelated repo's env (``GIT_DIR``, ``GIT_WORK_TREE``) can't skew the
    probe.

    Returns ``True``/``False`` when the probe ran conclusively, or ``None``
    when git itself couldn't be probed (binary missing, timeout) — an
    inconclusive probe must never be reported as proof of mis-registration.
    """
    env = {k: v for k, v in os.environ.items() if not k.startswith("GIT_")}
    try:
        bare = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--is-bare-repository"],
            capture_output=True, text=True, timeout=5, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if bare.stdout.strip() == "true":
        return True
    if bare.returncode != 0:
        return False
    try:
        top = subprocess.run(
            ["git", "-C", str(path), "rev-parse", "--show-toplevel"],
            capture_output=True, text=True, timeout=5, env=env,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if top.returncode != 0:
        return False
    try:
        return Path(top.stdout.strip()).resolve() == path.resolve()
    except OSError:
        return False


def mis_registered_repos(home_dir: Path | None = None) -> list[tuple[str, str, str]]:
    """``repos.yaml`` entries whose registered checkout doesn't actually resolve.

    Read-only counterpart to a ``repos.yaml`` entry going stale (moved,
    renamed, or never actually checked out on this machine) — surfaced by
    ``worktree-manager doctor`` alongside plugin-catalog alignment. Each
    result is ``(repo_name, status, detail)`` where ``status`` is one of:

    * ``"missing"`` — the registered path doesn't exist on disk.
    * ``"not-git"`` — the path exists but doesn't resolve to a real git
      checkout (confirmed by probing git itself).
    * ``"unknown"`` — the git probe itself couldn't run (git missing,
      timed out). This is **not** confirmed drift — `doctor`'s own
      prerequisite section separately reports git availability — but it
      must not be silently folded into a clean report either; the caller
      decides how to surface it.

    A repo without a path registered for *this exact* platform is not
    itself flagged here (a legitimate reference-only entry can be pathless
    on a given machine, and another platform's entry is never a stand-in
    for this one). A home-relative path (``~/src/repo``) is expanded before
    checking, matching how ``agent-worktrees`` itself resolves a registered
    path.
    """
    reg = repos_registry(home_dir)
    pkey = _exact_platform_key()
    findings: list[tuple[str, str, str]] = []
    for name, entry in (reg.get("repos") or {}).items():
        entry = entry or {}
        raw_path = entry.get(pkey)
        if not raw_path:
            continue
        path = Path(raw_path).expanduser()
        if not path.exists():
            findings.append((name, "missing", f"registered path does not exist: {raw_path}"))
            continue
        is_checkout = _is_real_git_checkout(path)
        if is_checkout is False:
            findings.append((name, "not-git", f"registered path is not a git checkout: {raw_path}"))
        elif is_checkout is None:
            findings.append((
                name, "unknown",
                f"could not verify (git probe failed/unavailable): {raw_path}",
            ))
    return findings



def build_projects(
    home_dir: Path | None = None, *, strict_rosters: bool = False,
    input_roots: ProfileInputRoots | None = None,
) -> list[ProjectInfo]:
    root_kwargs = {"input_roots": input_roots} if input_roots else {}
    reg = (projects_registry(home_dir, strict=True, **root_kwargs) if strict_rosters
           else projects_registry(home_dir, **root_kwargs))
    repos = {r.name: r for r in (
        build_repos(home_dir, strict=True, **root_kwargs) if strict_rosters
        else build_repos(home_dir, **root_kwargs)
    )}
    out: list[ProjectInfo] = []
    for name, entry in (reg.get("projects") or {}).items():
        entry = entry or {}
        project_dir = (input_roots.project_dir(name) if input_roots
                       else (home_dir or home()) / f".{name}")
        cfg = (_read_yaml(project_dir / "config.yaml", strict=strict_rosters)
               if strict_rosters or input_roots else project_config(name, home_dir))
        repo = repos.get(name)
        wsl = entry.get("wsl") if isinstance(entry.get("wsl"), dict) else {}
        # Prefer the repos.yaml-resolved path; fall back to this project's
        # own projects.yaml `anchor:` override for a project registered
        # without (or overriding) a repos.yaml entry.
        anchor = _selected_project_anchor(repo.path if repo else None, entry)
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
            roster=(project_roster(anchor, strict=True) if strict_rosters else project_roster(anchor)),
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
