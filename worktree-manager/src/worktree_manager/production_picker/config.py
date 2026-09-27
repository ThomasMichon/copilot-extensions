"""Manager-owned direct-file readers for production Picker project state."""

from __future__ import annotations

import contextlib
import threading
import time
from contextvars import ContextVar
from dataclasses import dataclass, field
from pathlib import Path

import yaml

from . import context
from .. import harness_state, terminal_fragment

_CANONICAL_INREPO_CONFIG = Path(".copilot-extensions") / "agent-worktrees" / "config.yaml"
_LEGACY_INREPO_CONFIG = Path(".agent-worktrees") / "config.yaml"
_LEGACY_SINGLE_FILE = Path(".agent-worktrees.yaml")
_MACHINES_CANONICAL = Path(".agent-worktrees") / "machines.yaml"
_MACHINES_LEGACY = Path("machines.yaml")
_DEFAULT_BRANCH = "master"
_CACHE_SESSION: ContextVar["ConfigCacheSession | None"] = ContextVar(
    "production_picker_config_cache_session", default=None
)


@dataclass(frozen=True)
class SSHEnvironment:
    name: str
    alias: str
    shell: str = ""


@dataclass(frozen=True)
class MachineEntry:
    key: str
    display_name: str
    environment: str
    alias: str = ""
    hostname: str = ""
    role: str = ""
    description: str = ""
    capabilities: list[str] = field(default_factory=list)
    ssh_environments: list[SSHEnvironment] = field(default_factory=list)
    ssh_ready: bool = False
    copilot: bool = True


@dataclass(frozen=True)
class RepoConfig:
    anchor: str
    default_branch: str = _DEFAULT_BRANCH


@dataclass(frozen=True)
class Config:
    repo_name: str
    machine: str = ""
    default_repo: RepoConfig = field(
        default_factory=lambda: RepoConfig(anchor="", default_branch=_DEFAULT_BRANCH)
    )


class ConfigCacheSession:
    """Tiny cross-thread cache for Picker setup/reload direct-file reads."""

    def __init__(self, ttl: float = 60.0):
        self.ttl = max(float(ttl), 0.0)
        self._lock = threading.Lock()
        self._values: dict[tuple[object, ...], tuple[float, object]] = {}

    def get_or_load(self, key: tuple[object, ...], loader):
        now = time.monotonic()
        with self._lock:
            cached = self._values.get(key)
            if cached is not None:
                written_at, value = cached
                if self.ttl <= 0 or (now - written_at) <= self.ttl:
                    return value
        value = loader()
        with self._lock:
            self._values[key] = (time.monotonic(), value)
        return value

    @contextlib.contextmanager
    def scope(self):
        token = _CACHE_SESSION.set(self)
        try:
            yield self
        finally:
            _CACHE_SESSION.reset(token)


def cached_load_config_scope():
    """One-pass helper matching the old proxy surface."""
    return ConfigCacheSession(ttl=0.0).scope()


def detect_platform() -> str:
    return terminal_fragment.detect_platform()


def _home() -> Path:
    return harness_state.home()


def install_dir() -> Path:
    return _home() / ".agent-worktrees"


def project_name() -> str:
    return context.project()


def project_dir(name: str | None = None) -> Path:
    return _home() / f".{name or project_name()}"


def default_config_path() -> Path:
    return project_dir() / "config.yaml"


def tracking_dir() -> Path:
    return project_dir() / "worktrees"


def _read_yaml(path: Path) -> dict:
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8"))
    except (OSError, yaml.YAMLError):
        return {}
    return data if isinstance(data, dict) else {}


def _active_project_info() -> harness_state.ProjectInfo | None:
    project = project_name()
    for info in harness_state.build_projects():
        if info.name == project:
            return info
    return None


def _active_anchor() -> str:
    info = _active_project_info()
    if info is not None and info.anchor:
        return info.anchor
    return str(Path.cwd())


def _repo_settings(anchor: str | Path) -> dict:
    root = Path(anchor)
    for rel in (_CANONICAL_INREPO_CONFIG, _LEGACY_INREPO_CONFIG, _LEGACY_SINGLE_FILE):
        path = root / rel
        if path.is_file():
            return _read_yaml(path)
    return {}


def _default_branch(anchor: str | Path) -> str:
    branch = str(_repo_settings(anchor).get("default_branch") or "").strip()
    return branch or _DEFAULT_BRANCH


def _load_config_uncached() -> Config:
    project = project_name()
    machine = str(harness_state.project_config(project).get("machine") or "").strip()
    anchor = _active_anchor()
    return Config(
        repo_name=project,
        machine=machine,
        default_repo=RepoConfig(
            anchor=anchor,
            default_branch=_default_branch(anchor),
        ),
    )


def load_config() -> Config:
    session = _CACHE_SESSION.get()
    if session is None:
        return _load_config_uncached()
    return session.get_or_load(("config", project_name()), _load_config_uncached)


def machines_yaml_path(repo_dir: str | Path) -> Path:
    root = Path(repo_dir)
    canonical = root / _MACHINES_CANONICAL
    if canonical.is_file():
        return canonical
    legacy = root / _MACHINES_LEGACY
    if legacy.is_file():
        return legacy
    return canonical


def _load_machines_yaml_uncached(repo_dir: str | Path) -> dict[str, MachineEntry]:
    path = machines_yaml_path(repo_dir)
    if not path.exists():
        raise FileNotFoundError(f"Machine registry not found at {path}")
    raw = _read_yaml(path)
    machines = raw.get("machines")
    if not isinstance(machines, dict):
        raise ValueError(f"machines.yaml at {path} is missing 'machines' key")

    entries: dict[str, MachineEntry] = {}
    for key, data in machines.items():
        if not isinstance(data, dict):
            continue
        ssh_block = data.get("ssh") if isinstance(data.get("ssh"), dict) else {}
        ssh_envs: list[SSHEnvironment] = []
        for env in ssh_block.get("environments", []) or []:
            if isinstance(env, dict) and "name" in env and "alias" in env:
                ssh_envs.append(
                    SSHEnvironment(
                        name=str(env["name"]),
                        alias=str(env["alias"]),
                        shell=str(env.get("shell") or ""),
                    )
                )
        caps: list[str] = []
        for capability in data.get("capabilities", []) or []:
            if isinstance(capability, str):
                cap = capability.strip()
                if cap and cap not in caps:
                    caps.append(cap)
        entries[str(key)] = MachineEntry(
            key=str(key),
            display_name=str(data.get("display_name") or key),
            environment=str(data.get("environment") or ""),
            alias=str(data.get("alias") or ""),
            hostname=str(data.get("hostname") or ""),
            role=str(data.get("role") or ""),
            description=str(data.get("description") or "").strip(),
            capabilities=caps,
            ssh_environments=ssh_envs,
            ssh_ready=bool(ssh_block.get("ready", False)),
            copilot=bool(data.get("copilot", True)),
        )
    return entries


def load_machines_yaml(repo_dir: str | Path) -> dict[str, MachineEntry]:
    key = ("machines", str(machines_yaml_path(repo_dir)))
    session = _CACHE_SESSION.get()
    if session is None:
        return _load_machines_yaml_uncached(repo_dir)
    return session.get_or_load(key, lambda: _load_machines_yaml_uncached(repo_dir))
