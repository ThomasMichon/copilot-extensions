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
from typing import Any

import yaml

from .manifest import ManifestError, RequirementPackage, load_package

MACHINE_STATE_ROOT = ".agent-machines"
ALL_PACKAGES_DIR = "all"
MACHINES_PACKAGES_DIR = "machines"
LEGACY_MACHINE_STATE_DIR = ".github/machine-state"
PROJECT_CONFIG_FILE = "config.yaml"
REPO_CONFIG_FILE = Path(".agent-worktrees") / "config.yaml"


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


def global_config_path(home_dir: Path | None = None) -> Path:
    return (home_dir or home()) / ".agent-worktrees" / PROJECT_CONFIG_FILE


@dataclass
class DiscoveredRepo:
    """A registered repo that carries applicable requirement packages."""

    name: str
    path: Path
    enabled: bool
    packages: list[RequirementPackage] = field(default_factory=list)


@dataclass(frozen=True)
class RepoCandidate:
    """One adopted or relationship-required repository considered for discovery."""

    name: str
    path: Path
    required_by: tuple[str, ...] = ()

    @property
    def required(self) -> bool:
        return bool(self.required_by)


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


def read_global_config(path: Path | None = None) -> dict:
    """Read the machine-wide agent-worktrees config, gracefully when absent."""
    path = path or global_config_path()
    if not path.is_file():
        return {}
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return config if isinstance(config, dict) else {}


def _project_config(project: Any) -> dict:
    """Read one adopted project's machine-local config without requiring agent-worktrees."""
    if not isinstance(project, dict) or not project.get("config_dir"):
        return {}
    path = Path(str(project["config_dir"])).expanduser() / PROJECT_CONFIG_FILE
    if not path.is_file():
        return {}
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return config if isinstance(config, dict) else {}


def _registered_repo_entry(repos: dict, name: str) -> tuple[str, dict] | None:
    """Return a canonical registry entry, matching repository names case-insensitively."""
    folded = name.casefold()
    for registered_name, entry in repos.items():
        if str(registered_name).casefold() == folded and isinstance(entry, dict):
            return str(registered_name), entry
    return None


def _repo_requires_external_state(repo_path: Path) -> bool:
    """Return whether committed repo config activates the knowledge relationship."""
    path = repo_path / REPO_CONFIG_FILE
    if not path.is_file():
        return False
    try:
        config = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return False
    if not isinstance(config, dict):
        return False
    return bool(config.get("stateless") or config.get("requires_external_state_root"))


def candidate_repos(
    registry: dict | None = None,
    projects: dict | None = None,
) -> list[RepoCandidate]:
    """Resolve adopted projects plus their declared supplemental package repositories.

    Adopted projects remain the discovery roots. A project's machine-local
    ``knowledge_repo`` binding contributes one required supplemental repository.
    Supplemental repositories must have a canonical ``repos.yaml`` entry. Once
    registered, normal registry resolution applies: an explicit platform path or
    the registry's declared ``srcroot``. An unregistered checkout at a conventional
    source-root path is never accepted.
    """
    proj = projects if projects is not None else read_projects()
    reg = registry if registry is not None else read_registry()
    if not isinstance(proj, dict) or not isinstance(reg, dict):
        return []
    project_entries = proj.get("projects") or {}
    if not isinstance(project_entries, dict):
        return []
    repos = reg.get("repos") or {}
    srcroot = reg.get("srcroot") or {}
    if not isinstance(repos, dict):
        repos = {}
    if not isinstance(srcroot, dict):
        srcroot = {}
    plat = current_platform()
    global_config = read_global_config()

    candidates: dict[str, RepoCandidate] = {}
    for raw_name in project_entries:
        name = str(raw_name)
        registered = _registered_repo_entry(repos, name)
        canonical_name = registered[0] if registered else name
        entry = registered[1] if registered else {}
        path = resolve_repo_path(canonical_name, entry, srcroot, plat)
        if path is not None:
            candidates.setdefault(
                canonical_name.casefold(),
                RepoCandidate(name=canonical_name, path=path),
            )

    for raw_project_name, project in project_entries.items():
        project_name = str(raw_project_name)
        project_candidate = candidates.get(project_name.casefold())
        if (
            project_candidate is None
            or not project_candidate.path.is_dir()
            or not _repo_requires_external_state(project_candidate.path)
        ):
            continue
        project_config = _project_config(project)
        knowledge_repo = (
            project_config.get("knowledge_repo")
            or global_config.get("knowledge_repo")
        )
        if not knowledge_repo:
            continue
        if not isinstance(knowledge_repo, str):
            raise ManifestError(
                f"project {project_name!r} config: knowledge_repo must be a repository name"
            )
        required_name = knowledge_repo.strip()
        if not required_name:
            continue
        registered = _registered_repo_entry(repos, required_name)
        if registered is None:
            raise ManifestError(
                f"project {project_name!r} binds supplemental repo {required_name!r}, "
                "but it has no canonical repos.yaml entry"
            )
        canonical_name, entry = registered
        path = resolve_repo_path(canonical_name, entry, srcroot, plat)
        if path is None:
            raise ManifestError(
                f"project {project_name!r} binds supplemental repo {canonical_name!r}, "
                f"but its repos.yaml entry has no path for platform {plat!r}"
            )
        folded = canonical_name.casefold()
        existing = candidates.get(folded)
        required_by = tuple(sorted(set((existing.required_by if existing else ()) + (project_name,))))
        candidates[folded] = RepoCandidate(
            name=canonical_name,
            path=path,
            required_by=required_by,
        )
    return list(candidates.values())


def repo_enables_agent_machines(repo_path: Path) -> bool:
    """True when the repo's copilot settings enable an ``agent-machines`` plugin.

    Reads the repo's plugin settings across both the Copilot-native and Claude
    conventions (native preferred, Claude fallback) via ``plugin_resolve`` so a
    repo that declares its config in ``.claude/settings.json`` is honored too.
    """
    from plugin_resolve import read_repo_settings

    enabled = read_repo_settings(repo_path).enabled
    return any(str(k).startswith("agent-machines") and v for k, v in enabled.items())


def _yaml_files(directory: Path) -> list[Path]:
    if not directory.is_dir():
        return []
    direct = sorted(directory.glob("*.yaml")) + sorted(directory.glob("*.yml"))
    nested = sorted(
        path for path in directory.rglob("*")
        if path.is_file()
        and path.suffix.casefold() in {".yaml", ".yml"}
        and path.parent != directory
    )
    if nested:
        paths = ", ".join(str(path) for path in nested)
        raise ManifestError(
            f"{directory}: requirement packages must be direct children; "
            f"nested YAML files found: {paths}"
        )
    return direct


def _validate_canonical_root(root: Path) -> None:
    allowed_dirs = {ALL_PACKAGES_DIR, MACHINES_PACKAGES_DIR}
    for child in sorted(root.iterdir()):
        if child.is_dir() and child.name in allowed_dirs:
            continue
        if child.is_file() and child.name.casefold() == "readme.md":
            continue
        raise ManifestError(
            f"{child}: unsupported entry under {MACHINE_STATE_ROOT}; "
            f"packages belong directly under {ALL_PACKAGES_DIR}/ or "
            f"{MACHINES_PACKAGES_DIR}/<machine>/"
        )


def _machine_package_dir(root: Path, machine: str) -> Path | None:
    machines_dir = root / MACHINES_PACKAGES_DIR
    if not machines_dir.is_dir():
        return None
    matches = sorted(
        child for child in machines_dir.iterdir()
        if child.is_dir() and child.name.casefold() == machine.casefold()
    )
    if len(matches) > 1:
        names = ", ".join(str(path) for path in matches)
        raise ManifestError(
            f"{machines_dir}: multiple machine directories match {machine!r}: {names}"
        )
    return matches[0] if matches else None


def package_files_in_repo(repo_path: Path, machine: str) -> list[Path]:
    """Resolve canonical package files, with a bounded legacy fallback.

    ``.agent-machines`` is authoritative whenever it exists. Adopters migrate
    atomically; the old ``.github/machine-state`` directory is consulted only
    when the canonical root is absent, so a moved package is never loaded twice.
    """
    root = repo_path / MACHINE_STATE_ROOT
    if root.is_dir():
        _validate_canonical_root(root)
        files = _yaml_files(root / ALL_PACKAGES_DIR)
        machine_dir = _machine_package_dir(root, machine)
        if machine_dir is not None:
            files.extend(_yaml_files(machine_dir))
        return files
    return _yaml_files(repo_path / LEGACY_MACHINE_STATE_DIR)


def packages_in_repo(repo_path: Path, repo_name: str, machine: str) -> list[RequirementPackage]:
    """Load and gate-filter the requirement packages carried by ``repo_path``."""
    out: list[RequirementPackage] = []
    names: dict[str, Path] = {}
    canonical = (repo_path / MACHINE_STATE_ROOT).is_dir()
    machine_root = repo_path / MACHINE_STATE_ROOT / MACHINES_PACKAGES_DIR
    for pkg_file in package_files_in_repo(repo_path, machine):
        pkg = load_package(pkg_file, source_repo=repo_name)
        machine_scoped = canonical and pkg_file.parent.parent == machine_root
        applies = pkg.applies_to(machine)
        if machine_scoped and not applies:
            raise ManifestError(
                f"{pkg_file}: package gate excludes its containing machine "
                f"directory {pkg_file.parent.name!r}; machine-scoped packages "
                "must omit gate, use '*', or include that machine"
            )
        if not applies:
            continue
        if canonical and pkg.name in names:
            raise ManifestError(
                f"{pkg_file}: package {pkg.name!r} duplicates {names[pkg.name]}; "
                "files under all/ and machines/<machine>/ are independent complete "
                "packages and must have unique package names"
            )
        names[pkg.name] = pkg_file
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
    it (a) carries ``.agent-machines/all/`` or
    ``.agent-machines/machines/<machine>/`` packages that (b) gate to
    ``machine``. Plugin-enable status is annotated; set ``require_enable`` to
    also require the project to enable ``agent-machines``. The legacy
    ``.github/machine-state/`` location is used only when ``.agent-machines/``
    is absent.
    """
    machine = machine or current_machine()
    found: list[DiscoveredRepo] = []
    for candidate in candidate_repos(registry, projects):
        name, path = candidate.name, candidate.path
        if not path.is_dir():
            if candidate.required:
                owners = ", ".join(candidate.required_by)
                raise ManifestError(
                    f"supplemental repo {name!r} required by {owners} is unavailable at {path}"
                )
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
              "(no adopted projects or declared supplemental repositories carry "
              ".agent-machines packages)")
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
