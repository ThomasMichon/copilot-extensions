"""Repos registry -- catalog of known repositories and source roots.

Manages ``~/.agent-worktrees/repos.yaml``, the canonical multi-repo
registry.  Each repo is tagged with a management *class* describing how
the facility interacts with its local checkout:

- **reference** -- read-only; tracked only for path resolution, cloning,
  and indexing.  Never edited locally.
- **singleton** -- editable as a single anchor checkout, no worktree
  isolation; one flow at a time.
- **worktree** -- full agent-worktrees lifecycle; concurrent-flow safe,
  with edits/stages/commits isolated in per-task worktrees until push.
  These are also adopted as ``projects.yaml`` projects.

The registry also stores per-platform source roots (``srcroot``) so that
adopt, WSL provision, and clone operations know where to put repos.

This registry supersedes the legacy ``~/.git-repos`` file; use
``repos migrate`` to import an existing ``~/.git-repos`` into it.
"""

from __future__ import annotations

import os
import platform
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import output

# ---------------------------------------------------------------------------
# Data model
# ---------------------------------------------------------------------------

# A repo's management class -- how the facility interacts with its checkout:
#
#   reference  Read-only.  Tracked only for path resolution, cloning, and
#              indexing (e.g. VEI).  Never edited locally.  (= external-repos
#              relationship "consumer".)
#   singleton  Editable as a single anchor checkout, with no worktree
#              isolation.  Use when only one flow edits at a time, or when
#              worktrees are overkill or unsupported.
#   worktree   Full agent-worktrees lifecycle: concurrent-flow safe; edits,
#              stages, and commits stay isolated in per-task worktrees until
#              the final push.  (= an adopted agent-worktrees "project".)
VALID_CLASSES = ("reference", "singleton", "worktree")

# Legacy ``type`` values mapped onto the new class taxonomy.
_LEGACY_TYPE_MAP = {"project": "worktree", "repo": "reference"}


def normalize_class(value: str | None) -> str:
    """Coerce a raw class/type string to a valid management class.

    Accepts the new class names (reference/singleton/worktree) and the
    legacy ``type`` values (project/repo).  Unknown values fall back to
    ``reference`` (the safest -- read-only) default.
    """
    if not value:
        return "reference"
    v = str(value).strip().lower()
    if v in VALID_CLASSES:
        return v
    return _LEGACY_TYPE_MAP.get(v, "reference")


@dataclass
class RepoEntry:
    """A single repo in the registry."""

    name: str
    repo_class: str = "reference"  # reference | singleton | worktree
    remote: str = ""
    default_branch: str = ""
    tags: list[str] = field(default_factory=list)
    contributing: str = ""
    # Whether this repo backs a same-machine agent in agent-bridge. Defaults
    # ON for worktree/singleton repos (you adopt them to work in them); OFF for
    # reference repos (read-only). `register`/`add --no-agent` forces it off.
    agent: bool = True
    paths: dict[str, str] = field(default_factory=dict)
    # paths keys: "windows", "wsl", "linux"

    def local_path(self, plat: str | None = None) -> str | None:
        """Return the path for the given (or current) platform."""
        plat = plat or _current_platform()
        return self.paths.get(plat)


@dataclass
class ReposRegistry:
    """The full repos.yaml content."""

    srcroot: dict[str, str] = field(default_factory=dict)
    # srcroot keys: "windows", "wsl", "linux"
    repos: dict[str, RepoEntry] = field(default_factory=dict)


# ---------------------------------------------------------------------------
# Platform helpers
# ---------------------------------------------------------------------------

def _current_platform() -> str:
    """Return 'windows', 'wsl', or 'linux'."""
    if platform.system() == "Windows":
        return "windows"
    if os.environ.get("WSL_DISTRO_NAME"):
        return "wsl"
    return "linux"


def _repos_yaml_path() -> Path:
    """Path to the repos registry file."""
    home = Path.home()
    return home / ".agent-worktrees" / "repos.yaml"


# ---------------------------------------------------------------------------
# Read / write
# ---------------------------------------------------------------------------

def read_registry() -> ReposRegistry:
    """Load repos.yaml, returning an empty registry if missing."""
    path = _repos_yaml_path()
    if not path.exists():
        return ReposRegistry()

    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return ReposRegistry()

        srcroot = data.get("srcroot", {})
        if not isinstance(srcroot, dict):
            srcroot = {}

        repos: dict[str, RepoEntry] = {}
        raw_repos = data.get("repos", {})
        if isinstance(raw_repos, dict):
            for name, entry in raw_repos.items():
                if not isinstance(entry, dict):
                    continue
                paths = {}
                for plat in ("windows", "wsl", "linux"):
                    if plat in entry:
                        paths[plat] = str(entry[plat])
                raw_tags = entry.get("tags", [])
                tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []
                # Prefer the new "class" field; fall back to legacy "type".
                raw_class = entry.get("class", entry.get("type"))
                norm_class = normalize_class(raw_class)
                # Agent exposure defaults ON for worktree/singleton, OFF for
                # reference; an explicit `agent:` always wins.
                raw_agent = entry.get("agent")
                agent = (
                    bool(raw_agent) if raw_agent is not None
                    else norm_class != "reference"
                )
                repos[name] = RepoEntry(
                    name=name,
                    repo_class=norm_class,
                    remote=entry.get("remote", ""),
                    default_branch=entry.get("default_branch", ""),
                    tags=tags,
                    contributing=entry.get("contributing", ""),
                    agent=agent,
                    paths=paths,
                )

        return ReposRegistry(srcroot=srcroot, repos=repos)
    except Exception:
        return ReposRegistry()


def write_registry(registry: ReposRegistry) -> None:
    """Write repos.yaml with hand-formatted YAML."""
    path = _repos_yaml_path()
    path.parent.mkdir(parents=True, exist_ok=True)

    lines = [
        "# ~/.agent-worktrees/repos.yaml",
        "# Registry of known repositories and source roots.",
        "",
    ]

    # srcroot section
    if registry.srcroot:
        lines.append("srcroot:")
        for plat in ("windows", "wsl", "linux"):
            if plat in registry.srcroot:
                lines.append(f"  {plat}: {_quote(registry.srcroot[plat])}")
        lines.append("")

    # repos section
    if registry.repos:
        lines.append("repos:")
        for name in sorted(registry.repos.keys()):
            entry = registry.repos[name]
            lines.append(f"  {name}:")
            lines.append(f"    class: {entry.repo_class}")
            # Emit `agent` only when it deviates from the class default
            # (worktree/singleton => on, reference => off) to keep files minimal.
            class_default_agent = entry.repo_class != "reference"
            if entry.agent != class_default_agent:
                lines.append(f"    agent: {'true' if entry.agent else 'false'}")
            if entry.remote:
                lines.append(f"    remote: {_quote(entry.remote)}")
            if entry.default_branch:
                lines.append(f"    default_branch: {_quote(entry.default_branch)}")
            if entry.tags:
                rendered = ", ".join(_quote(t) for t in entry.tags)
                lines.append(f"    tags: [{rendered}]")
            if entry.contributing:
                lines.append(f"    contributing: {_quote(entry.contributing)}")
            for plat in ("windows", "wsl", "linux"):
                if plat in entry.paths:
                    lines.append(f"    {plat}: {_quote(entry.paths[plat])}")
            lines.append("")

    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def _quote(v: str) -> str:
    """Quote a YAML string value if it contains special chars."""
    if any(c in v for c in (":", "#", "'", '"', "\\", "{", "}", "[", "]")):
        escaped = v.replace("\\", "\\\\").replace('"', '\\"')
        return f'"{escaped}"'
    return v


# ---------------------------------------------------------------------------
# Operations
# ---------------------------------------------------------------------------

def get_srcroot(plat: str | None = None) -> str | None:
    """Return the source root for the given (or current) platform."""
    plat = plat or _current_platform()
    registry = read_registry()
    return registry.srcroot.get(plat)


def set_srcroot(path: str, plat: str | None = None) -> None:
    """Set the source root for the given (or current) platform."""
    plat = plat or _current_platform()
    registry = read_registry()
    registry.srcroot[plat] = path
    write_registry(registry)
    output.ok(f"Source root for {plat} set to {path}")


def list_repos(class_filter: str | None = None) -> list[RepoEntry]:
    """Return all repos, optionally filtered by management class.

    The filter accepts new class names (reference/singleton/worktree) and
    legacy type values (project/repo), normalizing both.
    """
    registry = read_registry()
    entries = list(registry.repos.values())
    if class_filter:
        wanted = normalize_class(class_filter)
        entries = [e for e in entries if e.repo_class == wanted]
    return sorted(entries, key=lambda e: e.name)


def find_repo(name: str) -> RepoEntry | None:
    """Find a repo by name."""
    registry = read_registry()
    return registry.repos.get(name)


def add_repo(
    name: str,
    path: str,
    *,
    repo_class: str = "reference",
    remote: str = "",
    default_branch: str = "",
    tags: list[str] | None = None,
    contributing: str = "",
    agent: bool | None = None,
    plat: str | None = None,
) -> RepoEntry:
    """Register a repo at a known path.  Merges with existing entry."""
    plat = plat or _current_platform()
    registry = read_registry()
    repo_class = normalize_class(repo_class)

    existing = registry.repos.get(name)
    if existing:
        existing.paths[plat] = path
        if remote:
            existing.remote = remote
        # Only override class when explicitly upgraded away from the
        # default; this lets `add` re-register a path without clobbering
        # a deliberate classification.
        if repo_class != "reference":
            existing.repo_class = repo_class
        if default_branch:
            existing.default_branch = default_branch
        if tags:
            existing.tags = list(tags)
        if contributing:
            existing.contributing = contributing
        if agent is not None:
            existing.agent = agent
        entry = existing
    else:
        entry = RepoEntry(
            name=name,
            repo_class=repo_class,
            remote=remote,
            default_branch=default_branch,
            tags=list(tags) if tags else [],
            contributing=contributing,
            agent=agent if agent is not None else (repo_class != "reference"),
            paths={plat: path},
        )
        registry.repos[name] = entry

    write_registry(registry)
    agent_note = "" if entry.agent else " no-agent"
    output.ok(
        f"Repo '{name}' registered at {path} ({plat}) [{entry.repo_class}{agent_note}]"
    )
    return entry


def remove_repo(name: str) -> bool:
    """Remove a repo from the registry.  Returns True if it existed."""
    registry = read_registry()
    if name not in registry.repos:
        return False
    del registry.repos[name]
    write_registry(registry)
    output.ok(f"Repo '{name}' removed from registry")
    return True


def clone_repo(
    remote: str,
    name: str | None = None,
    target: str | None = None,
) -> RepoEntry | None:
    """Clone a repo to the srcroot (or target) and register it.

    Returns the new RepoEntry, or None on failure.
    """
    plat = _current_platform()

    # Infer name from remote URL if not provided
    if not name:
        name = _name_from_remote(remote)
    if not name:
        output.err("Cannot infer repo name from remote URL")
        return None

    # Determine target directory
    if not target:
        root = get_srcroot(plat)
        if not root:
            output.err(
                f"No srcroot configured for {plat}. "
                f"Set one with: agent-worktrees repos srcroot --set <path>"
            )
            return None
        target = str(Path(root).expanduser() / name)

    target_path = Path(target)
    if target_path.exists():
        output.warn(f"Directory already exists: {target}")
        # Still register it
        return add_repo(name, target, remote=remote)

    # Clone
    try:
        result = subprocess.run(
            ["git", "clone", remote, str(target_path)],
            capture_output=True,
            text=True,
            timeout=300,
        )
        if result.returncode != 0:
            output.err(f"git clone failed: {result.stderr.strip()}")
            return None
    except Exception as e:
        output.err(f"Clone failed: {e}")
        return None

    output.ok(f"Cloned {remote} to {target}")
    return add_repo(name, target, remote=remote)


def _name_from_remote(remote: str) -> str | None:
    """Extract a repo name from a git remote URL."""
    # Handle SSH: git@github.com:user/repo.git
    # Handle HTTPS: https://github.com/user/repo.git
    # Handle ADO: https://org.visualstudio.com/proj/_git/repo
    name = remote.rstrip("/")
    if name.endswith(".git"):
        name = name[:-4]
    # Take the last path segment
    name = name.rsplit("/", 1)[-1]
    # Handle SSH colon syntax
    if ":" in name:
        name = name.rsplit(":", 1)[-1]
    return name if name else None


def resolve_path(name: str, plat: str | None = None) -> str | None:
    """Resolve a repo name to its local path.

    Checks the registry first, then tries srcroot + name as a fallback.
    """
    plat = plat or _current_platform()
    entry = find_repo(name)
    if entry:
        p = entry.local_path(plat)
        if p:
            return p

    # Fallback: srcroot / name
    root = get_srcroot(plat)
    if root:
        candidate = Path(root).expanduser() / name
        if candidate.exists():
            return str(candidate)

    return None


# ---------------------------------------------------------------------------
# Migration from the legacy ~/.git-repos registry
# ---------------------------------------------------------------------------

def _git_repos_path() -> Path:
    """Path to the legacy ~/.git-repos registry file."""
    return Path.home() / ".git-repos"


def _adopted_project_names() -> set[str]:
    """Names of repos adopted as agent-worktrees projects (projects.yaml).

    Used by migration to classify adopted projects as ``worktree``.
    """
    projects_path = Path.home() / ".agent-worktrees" / "projects.yaml"
    if not projects_path.exists():
        return set()
    try:
        data = yaml.safe_load(projects_path.read_text(encoding="utf-8"))
        if isinstance(data, dict) and isinstance(data.get("projects"), dict):
            return set(data["projects"].keys())
    except Exception:
        pass
    return set()


def migrate_git_repos(
    *,
    default_class: str = "singleton",
    plat: str | None = None,
) -> tuple[int, int]:
    """Import the legacy ``~/.git-repos`` registry into ``repos.yaml``.

    The legacy file uses a single ``srcroot`` string and per-repo
    ``{remote, default_branch, tags, path, contributing}``.  Each entry is
    mapped onto the current platform in ``repos.yaml``:

    - ``srcroot`` -> ``srcroot[<platform>]``
    - per-repo ``path`` (or ``srcroot/<name>``) -> ``paths[<platform>]``
    - ``remote`` / ``default_branch`` / ``tags`` / ``contributing`` copied

    Management class is inferred: repos adopted as agent-worktrees
    projects become ``worktree``; everything else uses ``default_class``
    (``singleton`` by default -- a tracked, editable anchor checkout).

    Existing ``repos.yaml`` entries are merged, never clobbered: an
    already-set class is preserved.  ``~/.git-repos`` itself is left
    untouched.  Returns ``(migrated, skipped)`` counts.
    """
    plat = plat or _current_platform()
    src = _git_repos_path()
    if not src.exists():
        output.warn(f"No legacy registry found at {src}")
        return (0, 0)

    try:
        legacy = yaml.safe_load(src.read_text(encoding="utf-8"))
    except Exception as e:
        output.err(f"Could not parse {src}: {e}")
        return (0, 0)
    if not isinstance(legacy, dict):
        output.err(f"{src} is not a valid registry")
        return (0, 0)

    registry = read_registry()
    adopted = _adopted_project_names()

    # Map the legacy single srcroot onto the current platform.
    legacy_srcroot = legacy.get("srcroot")
    if isinstance(legacy_srcroot, str) and legacy_srcroot:
        registry.srcroot.setdefault(plat, legacy_srcroot)

    migrated = 0
    skipped = 0
    raw_repos = legacy.get("repos", {})
    if not isinstance(raw_repos, dict):
        raw_repos = {}

    for name, entry in raw_repos.items():
        if not isinstance(entry, dict):
            skipped += 1
            continue

        # Resolve the local path: explicit "path" wins, else srcroot/name.
        path = entry.get("path")
        if not path and isinstance(legacy_srcroot, str) and legacy_srcroot:
            path = str(Path(legacy_srcroot) / name)
        if not path:
            skipped += 1
            output.warn(f"  {name}: no path and no srcroot -- skipped")
            continue

        raw_tags = entry.get("tags", [])
        tags = [str(t) for t in raw_tags] if isinstance(raw_tags, list) else []

        # Infer class: adopted projects are worktree-managed; otherwise
        # the caller-provided default (singleton).
        inferred = "worktree" if name in adopted else default_class

        existing = registry.repos.get(name)
        if existing:
            existing.paths.setdefault(plat, str(path))
            if not existing.remote:
                existing.remote = entry.get("remote", "")
            if not existing.default_branch:
                existing.default_branch = entry.get("default_branch", "")
            if not existing.tags:
                existing.tags = tags
            if not existing.contributing:
                existing.contributing = entry.get("contributing", "")
            # Preserve an already-deliberate class; only fill if unset
            # (defaults to reference on a bare entry).
            if existing.repo_class == "reference" and inferred != "reference":
                existing.repo_class = normalize_class(inferred)
        else:
            registry.repos[name] = RepoEntry(
                name=name,
                repo_class=normalize_class(inferred),
                remote=entry.get("remote", ""),
                default_branch=entry.get("default_branch", ""),
                tags=tags,
                contributing=entry.get("contributing", ""),
                paths={plat: str(path)},
            )
        migrated += 1

    write_registry(registry)
    return (migrated, skipped)


# ---------------------------------------------------------------------------
# Multi-repo git hygiene (status / sync)
# ---------------------------------------------------------------------------

@dataclass
class RepoStatus:
    """Working-tree status for a single repo checkout."""

    name: str
    repo_class: str
    path: str | None = None
    present: bool = False
    branch: str = ""
    dirty: bool = False
    ahead: int = 0
    behind: int = 0
    error: str = ""


def _git(path: str, *args: str, timeout: int = 60) -> subprocess.CompletedProcess:
    return subprocess.run(
        ["git", "-C", path, *args],
        capture_output=True,
        text=True,
        timeout=timeout,
    )


def repo_status(entry: RepoEntry, plat: str | None = None) -> RepoStatus:
    """Compute working-tree status for one repo entry."""
    plat = plat or _current_platform()
    st = RepoStatus(name=entry.name, repo_class=entry.repo_class)
    path = entry.local_path(plat)
    st.path = path
    if not path or not (Path(path) / ".git").exists():
        return st
    st.present = True
    try:
        st.branch = _git(path, "branch", "--show-current").stdout.strip()
        st.dirty = bool(_git(path, "status", "--porcelain").stdout.strip())
        branch = entry.default_branch or st.branch
        if branch:
            counts = _git(
                path, "rev-list", "--left-right", "--count",
                f"origin/{branch}...HEAD",
            )
            if counts.returncode == 0:
                parts = counts.stdout.split()
                if len(parts) == 2:
                    st.behind, st.ahead = int(parts[0]), int(parts[1])
    except Exception as e:  # pragma: no cover - defensive
        st.error = str(e)
    return st


def _filter_repos(
    entries: list[RepoEntry],
    *,
    tag: str | None,
    class_filter: str | None,
) -> list[RepoEntry]:
    if class_filter:
        wanted = normalize_class(class_filter)
        entries = [e for e in entries if e.repo_class == wanted]
    if tag:
        entries = [e for e in entries if tag in e.tags]
    return entries


def status_all(
    *,
    tag: str | None = None,
    class_filter: str | None = None,
    plat: str | None = None,
) -> list[RepoStatus]:
    """Return status for all registered repos (optionally filtered)."""
    entries = _filter_repos(
        list(read_registry().repos.values()),
        tag=tag, class_filter=class_filter,
    )
    entries.sort(key=lambda e: e.name)
    return [repo_status(e, plat) for e in entries]


def sync_repo(entry: RepoEntry, plat: str | None = None) -> tuple[str, str]:
    """Fetch and fast-forward one repo's default branch.

    Returns ``(state, detail)`` where state is one of: ``synced``,
    ``skipped``, ``missing``, ``error``.  Dirty trees and detached/
    non-default branches are skipped (never force-updated).
    """
    plat = plat or _current_platform()
    path = entry.local_path(plat)
    if not path or not (Path(path) / ".git").exists():
        return ("missing", "not checked out")
    branch = entry.default_branch
    try:
        if _git(path, "status", "--porcelain").stdout.strip():
            return ("skipped", "working tree dirty")
        current = _git(path, "branch", "--show-current").stdout.strip()
        if not current:
            # Detached HEAD (e.g. a reference repo pinned at a tag/commit):
            # never fast-forward it.
            return ("skipped", "detached HEAD")
        if branch and current != branch:
            return ("skipped", f"on '{current}', not '{branch}'")
        target = branch or current
        fetch = _git(path, "fetch", "origin", timeout=180)
        if fetch.returncode != 0:
            return ("error", fetch.stderr.strip() or "fetch failed")
        if not target:
            return ("skipped", "no branch to fast-forward")
        ff = _git(path, "merge", "--ff-only", f"origin/{target}")
        if ff.returncode != 0:
            return ("skipped", "not fast-forwardable (diverged)")
        return ("synced", target)
    except Exception as e:
        return ("error", str(e))


def sync_all(
    *,
    tag: str | None = None,
    class_filter: str | None = None,
    plat: str | None = None,
) -> list[tuple[str, str, str]]:
    """Fetch + ff-merge all registered repos (optionally filtered).

    Returns a list of ``(name, state, detail)`` tuples.
    """
    entries = _filter_repos(
        list(read_registry().repos.values()),
        tag=tag, class_filter=class_filter,
    )
    entries.sort(key=lambda e: e.name)
    results = []
    for e in entries:
        state, detail = sync_repo(e, plat)
        results.append((e.name, state, detail))
    return results
