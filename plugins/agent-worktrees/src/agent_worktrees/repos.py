"""Repos registry -- catalog of known repositories and source roots.

Manages ``~/.agent-worktrees/repos.yaml``, a two-tier registry:

- **project** repos get full agent-worktrees management (binstubs,
  worktrees, terminal profiles).  These are also in ``projects.yaml``.
- **repo** entries are tracked locations only -- used for path lookup,
  clone resolution, and future ACP bridge dispatch.

The registry also stores per-platform source roots (``srcroot``) so
that adopt, WSL provision, and clone operations know where to put repos.
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

@dataclass
class RepoEntry:
    """A single repo in the registry."""

    name: str
    type: str = "repo"  # "project" or "repo"
    remote: str = ""
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
                repos[name] = RepoEntry(
                    name=name,
                    type=entry.get("type", "repo"),
                    remote=entry.get("remote", ""),
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
            lines.append(f"    type: {entry.type}")
            if entry.remote:
                lines.append(f"    remote: {_quote(entry.remote)}")
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


def list_repos(type_filter: str | None = None) -> list[RepoEntry]:
    """Return all repos, optionally filtered by type."""
    registry = read_registry()
    entries = list(registry.repos.values())
    if type_filter:
        entries = [e for e in entries if e.type == type_filter]
    return sorted(entries, key=lambda e: e.name)


def find_repo(name: str) -> RepoEntry | None:
    """Find a repo by name."""
    registry = read_registry()
    return registry.repos.get(name)


def add_repo(
    name: str,
    path: str,
    *,
    type: str = "repo",
    remote: str = "",
    plat: str | None = None,
) -> RepoEntry:
    """Register a repo at a known path.  Merges with existing entry."""
    plat = plat or _current_platform()
    registry = read_registry()

    existing = registry.repos.get(name)
    if existing:
        existing.paths[plat] = path
        if remote:
            existing.remote = remote
        if type != "repo":
            existing.type = type
        entry = existing
    else:
        entry = RepoEntry(
            name=name,
            type=type,
            remote=remote,
            paths={plat: path},
        )
        registry.repos[name] = entry

    write_registry(registry)
    output.ok(f"Repo '{name}' registered at {path} ({plat})")
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
