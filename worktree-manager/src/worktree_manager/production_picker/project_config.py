"""Manager-owned project config/path readers for the production Picker.

This mirrors only the small subset of ``agent_worktrees.config`` the
transplanted Picker still needs after Phase 3d Group C, but does so by reading
stable files and pinned engine scalars over the existing process boundary
instead of importing the engine in-process.
"""

from __future__ import annotations

import contextlib
from dataclasses import dataclass, field
from functools import lru_cache
from pathlib import Path

import yaml

from .. import engine_client, harness_state, terminal_fragment
from . import context, engine_group_a


@dataclass(frozen=True)
class SSHEnvironment:
    name: str
    alias: str
    shell: str = ""


@dataclass(frozen=True)
class MachineEntry:
    key: str
    display_name: str
    environment: str = ""
    alias: str = ""
    hostname: str = ""
    ssh_environments: list[SSHEnvironment] = field(default_factory=list)
    ssh_ready: bool = False
    copilot: bool = True


@dataclass(frozen=True)
class RepoConfig:
    anchor: str
    default_branch: str = ""


@dataclass(frozen=True)
class ProjectConfig:
    repo_name: str
    machine: str
    default_repo: RepoConfig


class ConfigCacheSession:
    """Compatibility surface for the Picker's setup worker cache scope.

    The Manager now reads its small config subset directly and memoizes it at the
    module level, so the old engine-owned TTL session collapses to a no-op
    context manager that still matches the call sites' shape.
    """

    def __init__(self, ttl: float = 0.0) -> None:
        del ttl

    def scope(self):
        return contextlib.nullcontext(self)


def cached_load_config_scope():
    return contextlib.nullcontext(ConfigCacheSession())


def clear_caches() -> None:
    _project_dir.cache_clear()
    _repo_dir.cache_clear()
    _machine_name.cache_clear()
    _install_dir.cache_clear()
    _load_project_yaml.cache_clear()
    _load_repo_yaml.cache_clear()
    _load_machines_yaml.cache_clear()


@lru_cache(maxsize=None)
def _project_dir(project: str) -> Path:
    try:
        value = engine_client.get_value(project, "config-dir")
    except engine_client.EngineError:
        value = ""
    if value:
        return Path(value)
    return harness_state.home() / f".{project}"


@lru_cache(maxsize=None)
def _repo_dir(project: str) -> str:
    try:
        value = engine_client.get_value(project, "repo-dir")
    except engine_client.EngineError:
        value = ""
    if value:
        return value
    for info in harness_state.build_projects():
        if info.name == project and info.anchor:
            return info.anchor
    return ""


@lru_cache(maxsize=None)
def _machine_name(project: str) -> str:
    try:
        value = engine_client.get_value(project, "machine")
    except engine_client.EngineError:
        value = ""
    if value:
        return value
    return str(harness_state.project_config(project).get("machine") or "")


@lru_cache(maxsize=None)
def _install_dir(project: str) -> Path:
    payload = engine_group_a.picker_paths(project)
    return Path(str(payload.get("install_dir") or ""))


def project_name() -> str:
    return context.project()


def project_dir(project: str | None = None) -> Path:
    return _project_dir(project or project_name())


def default_config_path(project: str | None = None) -> Path:
    return project_dir(project) / "config.yaml"


def tracking_dir(project: str | None = None) -> Path:
    return project_dir(project) / "worktrees"


def install_dir(project: str | None = None) -> Path:
    return _install_dir(project or project_name())


def detect_platform() -> str:
    return terminal_fragment.detect_platform()


def _read_yaml(path: Path) -> dict:
    try:
        value = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
    except (OSError, yaml.YAMLError):
        return {}
    return value if isinstance(value, dict) else {}


@lru_cache(maxsize=None)
def _load_project_yaml(project: str) -> dict:
    return _read_yaml(default_config_path(project))


@lru_cache(maxsize=None)
def _load_repo_yaml(project: str) -> dict:
    repo_dir = _repo_dir(project).strip()
    if not repo_dir:
        return {}
    root = Path(repo_dir)
    for candidate in (
        root / ".agent-worktrees" / "config.yaml",
        root / "config.yaml",
    ):
        if candidate.is_file():
            return _read_yaml(candidate)
    return {}


def _parse_machines_yaml_file(path: Path) -> dict[str, MachineEntry]:
    """Parse one ``machines.yaml`` file's ``machines:`` block into entries."""
    raw = _read_yaml(path)
    machines = raw.get("machines")
    if not isinstance(machines, dict):
        raise ValueError(f"machines.yaml at {path} is missing 'machines' key")
    entries: dict[str, MachineEntry] = {}
    for key, value in machines.items():
        if not isinstance(value, dict):
            continue
        ssh = value.get("ssh") if isinstance(value.get("ssh"), dict) else {}
        envs = []
        for env in ssh.get("environments") or []:
            if not isinstance(env, dict):
                continue
            name = str(env.get("name") or "").strip()
            alias = str(env.get("alias") or "").strip()
            if not name:
                continue
            envs.append(
                SSHEnvironment(
                    name=name,
                    alias=alias,
                    shell=str(env.get("shell") or "").strip(),
                )
            )
        entry = MachineEntry(
            key=str(key),
            display_name=str(value.get("display_name") or key),
            environment=str(value.get("environment") or ""),
            alias=str(value.get("alias") or ""),
            hostname=str(value.get("hostname") or ""),
            ssh_environments=envs,
            ssh_ready=bool(ssh.get("ready")),
            copilot=bool(value.get("copilot", True)),
        )
        entries[entry.key] = entry
    return entries


@lru_cache(maxsize=None)
def _load_machines_yaml(repo_dir: str) -> dict[str, MachineEntry]:
    """Additive merge of the canonical in-repo ``.agent-worktrees/
    machines.yaml`` (agent-containers fleet registrations, etc.) with the
    legacy repo-root ``machines.yaml`` (the real SSH-mesh roster) when both
    exist -- the canonical file's own header documents this as strictly
    additive ("never shadows or duplicates an entry already in the root
    file"), so returning only whichever file ``machines_yaml_path`` happened
    to resolve first (the prior behavior) silently dropped every real
    facility machine the moment a canonical file existed (confirmed
    regression from ThomasMichon/copilot-extensions#7914 -- a Windows
    Worktree Manager Picker only showed its own local tab, no remote
    machines, the instant the in-repo file was added). On a key collision
    the canonical in-repo entry wins; collisions aren't expected since the
    two files are meant to carry disjoint keys.
    """
    root = Path(repo_dir)
    canonical = root / ".agent-worktrees" / "machines.yaml"
    legacy = root / "machines.yaml"
    canonical_exists = canonical.is_file()
    legacy_exists = legacy.is_file()
    if not canonical_exists and not legacy_exists:
        path = machines_yaml_path(repo_dir)  # overlay fallback, or the error path
        if not path.is_file():
            raise FileNotFoundError(f"Machine registry not found at {path}")
        return _parse_machines_yaml_file(path)
    entries: dict[str, MachineEntry] = {}
    if legacy_exists:
        entries.update(_parse_machines_yaml_file(legacy))
    if canonical_exists:
        entries.update(_parse_machines_yaml_file(canonical))
    return entries


def load_config() -> ProjectConfig:
    project = project_name()
    project_yaml = _load_project_yaml(project)
    repo_yaml = _load_repo_yaml(project)
    repo_name = str(project_yaml.get("repo_name") or project).strip() or project
    machine = _machine_name(project).strip()
    repo_dir = _repo_dir(project).strip()
    default_branch = str(repo_yaml.get("default_branch") or "").strip()
    if not default_branch:
        repos = harness_state.repos_registry().get("repos") or {}
        entry = repos.get(project)
        if isinstance(entry, dict):
            default_branch = str(entry.get("default_branch") or "").strip()
    return ProjectConfig(
        repo_name=repo_name,
        machine=machine,
        default_repo=RepoConfig(anchor=repo_dir, default_branch=default_branch),
    )


def load_machines_yaml(repo_dir: str | Path) -> dict[str, MachineEntry]:
    return _load_machines_yaml(str(repo_dir))


def machines_yaml_path(repo_dir: str | Path) -> Path:
    root = Path(repo_dir)
    project = project_name()
    for candidate in (
        root / ".agent-worktrees" / "machines.yaml",
        root / "machines.yaml",
    ):
        if candidate.is_file():
            return candidate
    knowledge_repo = str(harness_state.project_config(project).get("knowledge_repo") or "").strip()
    if knowledge_repo:
        for info in harness_state.build_projects():
            if info.name != knowledge_repo or not info.anchor:
                continue
            overlay_root = Path(info.anchor)
            for candidate in (
                overlay_root / ".agent-worktrees" / "machines.yaml",
                overlay_root / "machines.yaml",
            ):
                if candidate.is_file():
                    return candidate
    return root / ".agent-worktrees" / "machines.yaml"
