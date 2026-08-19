"""Discover this machine's requirement-package set.

``~/.copilot/`` is machine-global, but a machine can host several harness
*projects*. So restore is **machine-scoped**: the union of every discovered
package, not the current anchor.

Discovery is scoped to **adopted projects** -- the ``projects`` in
``~/.agent-worktrees/projects.yaml`` (the adoption/launch registry) -- not every
cloned repo. That registry is name-keyed to ``repos.yaml``, which remains the
single owning store of each project's path; discovery reads projects.yaml for the
*candidate set* and repos.yaml only to *resolve paths*. Both are upstream-owned
facts, so the discovery set is never state ``agent-machines`` itself manages (no
recursion).

À la carte independence: if the registry is absent (agent-worktrees not
installed), discovery degrades to an empty set. Discovery never *requires* a
sibling plugin.
"""

from __future__ import annotations

import os
import platform
import sys
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from .manifest import RequirementPackage, load_package

MACHINE_STATE_DIR = ".github/machine-state"


def home() -> Path:
    return Path(os.path.expanduser("~"))


def current_machine() -> str:
    """The machine name used to gate packages (matches the harness convention)."""
    return platform.node()


def current_platform() -> str:
    """Return the registry path-key for this platform: windows | wsl | linux."""
    if os.name == "nt":
        return "windows"
    release = platform.release().lower()
    if "microsoft" in release or "wsl" in release:
        return "wsl"
    return "linux"


def registry_path(home_dir: Path | None = None) -> Path:
    return (home_dir or home()) / ".agent-worktrees" / "repos.yaml"


def projects_path(home_dir: Path | None = None) -> Path:
    return (home_dir or home()) / ".agent-worktrees" / "projects.yaml"


@dataclass
class DiscoveredRepo:
    """A registered repo that carries applicable requirement packages."""

    name: str
    path: Path
    enabled: bool
    packages: list[RequirementPackage] = field(default_factory=list)


def resolve_repo_path(name: str, entry: dict, srcroot: dict, plat: str) -> Path | None:
    """Resolve a repo's checkout path on ``plat`` from its registry entry.

    Paths in ``repos.yaml`` may be written with a ``~`` home shorthand (e.g. a
    WSL entry ``wsl: ~/src/aperture-labs``), so every resolved path is
    ``expanduser()``-ed. Without this, ``Path('~/src/...').is_dir()`` is False in
    :func:`discover`, the repo is silently skipped, and none of its machine-state
    packages are discovered.
    """
    if isinstance(entry, dict) and entry.get(plat):
        return Path(str(entry[plat])).expanduser()
    root = srcroot.get(plat)
    if root:
        return (Path(str(root)) / name).expanduser()
    return None


def read_registry(path: Path | None = None) -> dict:
    """Read ``repos.yaml`` (graceful): return ``{}`` when it is missing/unreadable."""
    path = path or registry_path()
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def read_projects(path: Path | None = None) -> dict:
    """Read ``projects.yaml`` (graceful): return ``{}`` when missing/unreadable."""
    path = path or projects_path()
    if not path.exists():
        return {}
    try:
        return yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}


def repo_enables_agent_machines(repo_path: Path) -> bool:
    """True when the repo's copilot settings enable an ``agent-machines`` plugin.

    Reads the repo's plugin settings across both the Copilot-native and Claude
    conventions (native preferred, Claude fallback) via ``plugin_resolve`` so a
    repo that declares its config in ``.claude/settings.json`` is honored too.
    """
    from plugin_resolve import read_repo_settings

    enabled = read_repo_settings(repo_path).enabled
    return any(str(k).startswith("agent-machines") and v for k, v in enabled.items())


def packages_in_repo(repo_path: Path, repo_name: str, machine: str) -> list[RequirementPackage]:
    """Load and gate-filter the requirement packages carried by ``repo_path``."""
    state_dir = repo_path / MACHINE_STATE_DIR
    if not state_dir.is_dir():
        return []
    out: list[RequirementPackage] = []
    for pkg_file in sorted(state_dir.glob("*.yaml")) + sorted(state_dir.glob("*.yml")):
        pkg = load_package(pkg_file, source_repo=repo_name)
        if pkg.applies_to(machine):
            out.append(pkg)
    return out


def discover(
    machine: str | None = None,
    registry: dict | None = None,
    projects: dict | None = None,
    require_enable: bool = False,
) -> list[DiscoveredRepo]:
    """Return the adopted projects on this machine that contribute packages.

    The candidate set is ``projects.yaml`` (adopted harness projects); each path
    is resolved from ``repos.yaml`` (which owns paths). A project is included when
    it (a) carries ``.github/machine-state/`` packages that (b) gate to
    ``machine``. Plugin-enable status is annotated; set ``require_enable`` to also
    require the project to enable ``agent-machines``.
    """
    machine = machine or current_machine()
    proj = projects if projects is not None else read_projects()
    reg = registry if registry is not None else read_registry()
    project_names = (proj.get("projects") or {}).keys()
    repos = reg.get("repos") or {}
    srcroot = reg.get("srcroot") or {}
    plat = current_platform()

    found: list[DiscoveredRepo] = []
    for name in project_names:
        entry = repos.get(name) or {}
        path = resolve_repo_path(name, entry if isinstance(entry, dict) else {}, srcroot, plat)
        if not path or not path.is_dir():
            continue
        pkgs = packages_in_repo(path, name, machine)
        if not pkgs:
            continue
        enabled = repo_enables_agent_machines(path)
        if require_enable and not enabled:
            continue
        found.append(DiscoveredRepo(name=name, path=path, enabled=enabled, packages=pkgs))
    return found


def _main(argv: list[str] | None = None) -> int:  # pragma: no cover - thin CLI glue
    machine = current_machine()
    repos = discover(machine)
    print(f"machine: {machine}  platform: {current_platform()}")
    if not repos:
        print("no requirement packages discovered "
              "(no adopted projects in ~/.agent-worktrees/projects.yaml, "
              "or none carry .github/machine-state/)")
        return 0
    for repo in repos:
        flag = "enabled" if repo.enabled else "not-enabled"
        print(f"  {repo.name}  [{flag}]  ({repo.path})")
        for pkg in repo.packages:
            keys = ", ".join(sorted(pkg.manage)) or "(no managed keys)"
            print(f"      package {pkg.name}: {keys}")
    return 0


if __name__ == "__main__":  # pragma: no cover
    sys.exit(_main())
