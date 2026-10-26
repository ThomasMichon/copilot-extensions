"""Manager-owned project config/path readers for the production Picker.

This mirrors only the small subset of ``agent_worktrees.config`` the
transplanted Picker still needs after Phase 3d Group C, but does so by reading
stable files and pinned engine scalars over the existing process boundary
instead of importing the engine in-process.
"""

from __future__ import annotations

import contextlib
import asyncio
from collections import OrderedDict
from contextvars import ContextVar
from copy import deepcopy
from dataclasses import dataclass
from functools import lru_cache
import math
from pathlib import Path
import threading
from time import monotonic as _file_clock

import yaml
from machine_transport import MachineEntry, SSHEnvironment  # noqa: F401 (re-exported)
from machine_transport import parse_machines_yaml_file as _mt_parse_machines_yaml_file

from .. import engine_client, harness_state, machine_roster, terminal_fragment
from . import context, engine_group_a

class _ScalarCache:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.values: dict[tuple[str, str], str] = {}
        self.fallbacks: dict[tuple[str, str], str] = {}
        self.failures: dict[tuple[str, str], int] = {}
        self.epoch = 0
        self.machine_bindings: dict[str, str | None] = {}


_scalar_cache = _ScalarCache()


def _input_scalar(cache: _ScalarCache, project: str, key: str) -> str | None:
    """Keep the last worker-prepared value usable while a reload retries."""
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return None
    identity = (project, key)
    return cache.values.get(identity, cache.fallbacks.get(identity))


def bind_bootstrap(project: str, values: object) -> None:
    """Adopt optional engine scalars; older engines keep the memoized fallback."""
    global _scalar_cache
    cache = _ScalarCache()
    if isinstance(values, dict):
        if "configured_machine" in values:
            binding = values["configured_machine"]
            if binding is not None and (
                not isinstance(binding, str) or not binding.strip()
            ):
                raise ValueError("configured machine binding must be a nonempty string")
            cache.machine_bindings[project] = binding
        for field, key in (
            ("config_dir", "config-dir"), ("repo_dir", "repo-dir"), ("machine", "machine"),
            ("install_dir", "install-dir"),
        ):
            value = values.get(field)
            if not isinstance(value, str) or not value.strip():
                continue
            if field != "machine" and not Path(value).is_absolute():
                continue
            cache.values[project, key] = value
    # Old readers retain only their old cache, including its in-flight lock.
    _scalar_cache = cache
    clear_caches()


def _scalar(cache: _ScalarCache, epoch: int, project: str, key: str) -> str:
    # lru_cache alone permits concurrent cold misses from pivot/source workers.
    identity = (project, key)
    value = cache.values.get(identity)
    if value is not None:
        return value
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        pass
    else:
        raise RuntimeError("Picker configuration is not ready; wait for setup or reload")
    with cache.lock:
        if identity not in cache.values:
            if cache.failures.get(identity) == epoch:
                return ""
            from .picker_tui.frame_health import append_launch_event

            append_launch_event("picker_config_scalar_fallback", key=key)
            try:
                value = engine_client.get_value(project, key)
            except engine_client.EngineError:
                cache.failures[identity] = epoch
                return ""
            if not value:
                cache.failures[identity] = epoch
                return ""
            cache.values[identity] = value
        return cache.values[identity]

# ``SSHEnvironment``/``MachineEntry`` are the shared ``machine_transport``
# library's own classes (vendored, not duplicated -- see its README's "Why
# this exists"), re-exported here under their historical names so every
# existing reference in this package keeps working unchanged.


@dataclass(frozen=True)
class RepoConfig:
    anchor: str
    default_branch: str = ""


@dataclass(frozen=True)
class ProjectConfig:
    repo_name: str
    machine: str
    default_repo: RepoConfig
    configured_machine: str | None = None
    machine_binding_known: bool = False


def _on_event_loop() -> bool:
    try:
        asyncio.get_running_loop()
    except RuntimeError:
        return False
    return True


@dataclass(frozen=True)
class _YamlSnapshot:
    content: bytes | None
    value: dict


class _FileEntry:
    def __init__(self) -> None:
        self.finished = threading.Event()
        self.value = None
        self.error: BaseException | None = None
        self.ready = False


class _FileGeneration:
    """One bounded snapshot generation; only workers join in-flight reads."""

    capacity = 128

    def __init__(self, binding, epoch, ttl, previous=None):
        self.binding = binding
        self.epoch = epoch
        self.ttl = ttl
        self.created = _file_clock()
        self.invalidated = False
        self.lock = threading.Lock()
        self.entries = OrderedDict()
        self.seeds = {}
        if previous is not None:
            with previous.lock:
                self.seeds = {
                    key: entry.value for key, entry in previous.entries.items()
                    if entry.ready and isinstance(entry.value, _YamlSnapshot)
                }

    def current(self):
        return (
            not self.invalidated and self.binding is _scalar_cache
            and self.epoch == self.binding.epoch
        )

    def expired(self):
        return self.ttl > 0 and _file_clock() - self.created >= self.ttl

    def get(self, key, loader):
        if not self.current():
            raise RuntimeError("Picker file configuration is not ready; generation superseded")
        if _on_event_loop():
            entry = self.entries.get(key)
            if entry is None or not entry.ready:
                raise RuntimeError("Picker file configuration is not ready; refresh on a worker")
        else:
            with self.lock:
                entry = self.entries.get(key)
                owner = entry is None
                if owner:
                    if len(self.entries) >= self.capacity:
                        raise RuntimeError("Picker file configuration read capacity is exhausted")
                    entry = self.entries[key] = _FileEntry()
                else:
                    self.entries.move_to_end(key)
            if owner:
                try:
                    value = loader()
                    if not self.current():
                        raise RuntimeError("Picker file configuration is not ready; generation superseded")
                    entry.value = value
                except BaseException as exc:
                    entry.error = exc
                finally:
                    entry.ready = True
                    entry.finished.set()
            else:
                entry.finished.wait()
        if not self.current():
            raise RuntimeError("Picker file configuration is not ready; generation superseded")
        if entry.error is not None:
            errors = _active_file_errors.get()
            if errors is not None:
                errors[0] = entry.error
            raise entry.error
        return entry.value


_active_files = ContextVar("picker_file_generation", default=None)
_active_file_errors = ContextVar("picker_file_errors", default=None)
_file_generation = None
_file_generation_lock = threading.Lock()


def _invalidate_file_cache():
    global _file_generation
    previous = _file_generation
    _file_generation = None
    if previous is not None:
        previous.invalidated = True


def _current_file_generation(ttl):
    global _file_generation
    scoped = _active_files.get()
    if scoped is not None:
        return scoped
    current = _file_generation
    if _on_event_loop():
        if ttl == 0 or current is None or not current.current() or current.expired():
            raise RuntimeError("Picker file configuration is not ready; refresh on a worker")
        return current
    with _file_generation_lock:
        current = _file_generation
        if ttl == 0:
            return _FileGeneration(_scalar_cache, _scalar_cache.epoch, ttl)
        if current is None or not current.current() or current.expired() or current.ttl != ttl:
            same_binding = current is not None and current.current()
            current = _FileGeneration(
                _scalar_cache, _scalar_cache.epoch, ttl,
                previous=current if same_binding else None,
            )
            _file_generation = current
        return current


class ConfigCacheSession:
    """Pin a file generation across one logical worker pass."""

    def __init__(self, ttl: float = 0.0) -> None:
        if not math.isfinite(ttl) or ttl < 0:
            raise ValueError("configuration cache TTL must be finite and nonnegative")
        self.ttl = ttl

    @property
    def revision(self):
        generation = _active_files.get() or _file_generation
        if generation is None or not generation.current() or generation.expired():
            return None
        return generation

    @contextlib.contextmanager
    def scope(self):
        generation = _current_file_generation(self.ttl)
        token = _active_files.set(generation)
        errors = _active_file_errors.get()
        error_token = _active_file_errors.set(errors if errors is not None else [None])
        try:
            yield self
        finally:
            _active_file_errors.reset(error_token)
            _active_files.reset(token)

    def raise_for_errors(self):
        errors = _active_file_errors.get()
        if errors is not None and errors[0] is not None:
            raise errors[0]


def cached_load_config_scope():
    return ConfigCacheSession().scope()


@contextlib.contextmanager
def _file_scope():
    with ConfigCacheSession(ttl=60.0).scope():
        yield _active_files.get()


def clear_caches(*, reset_binding: bool = False) -> None:
    """Refresh file snapshots without discarding the accepted engine binding."""
    global _scalar_cache
    if reset_binding:
        _scalar_cache = _ScalarCache()
    _scalar_cache.epoch += 1
    _cached_project_dir.cache_clear()
    _cached_repo_dir.cache_clear()
    _cached_machine_name.cache_clear()
    _install_dir.cache_clear()
    _cached_project_yaml.cache_clear()
    _cached_repo_yaml.cache_clear()
    _load_machines_yaml.cache_clear()
    _invalidate_file_cache()


def _project_dir(project: str) -> Path:
    cache = _scalar_cache
    value = _input_scalar(cache, project, "config-dir")
    if value is not None:
        return Path(value)
    return _cached_project_dir(cache, cache.epoch, project)


@lru_cache(maxsize=None)
def _cached_project_dir(cache: _ScalarCache, epoch: int, project: str) -> Path:
    value = _scalar(cache, epoch, project, "config-dir")
    if value:
        return Path(value)
    fallback = harness_state.home() / f".{project}"
    cache.fallbacks[project, "config-dir"] = str(fallback)
    return fallback


def _repo_dir(project: str) -> str:
    cache = _scalar_cache
    value = _input_scalar(cache, project, "repo-dir")
    if value is not None:
        return value
    return _cached_repo_dir(cache, cache.epoch, project)


@lru_cache(maxsize=None)
def _cached_repo_dir(cache: _ScalarCache, epoch: int, project: str) -> str:
    value = _scalar(cache, epoch, project, "repo-dir")
    if value:
        return value
    for info in harness_state.build_projects():
        if info.name == project and info.anchor:
            cache.fallbacks[project, "repo-dir"] = info.anchor
            return info.anchor
    cache.fallbacks[project, "repo-dir"] = ""
    return ""


def _machine_name(project: str) -> str:
    cache = _scalar_cache
    value = _input_scalar(cache, project, "machine")
    if value is not None:
        return value
    return _cached_machine_name(cache, cache.epoch, project)


@lru_cache(maxsize=None)
def _cached_machine_name(cache: _ScalarCache, epoch: int, project: str) -> str:
    value = _scalar(cache, epoch, project, "machine")
    if value:
        return value
    fallback = str(harness_state.project_config(project).get("machine") or "")
    cache.fallbacks[project, "machine"] = fallback
    return fallback


@lru_cache(maxsize=None)
def _install_dir(project: str) -> Path:
    bound = _scalar_cache.values.get((project, "install-dir"))
    if bound is not None:
        return Path(bound)
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
    return deepcopy(_yaml_snapshot(path).value)


def _yaml_snapshot(path: Path) -> _YamlSnapshot:
    path = Path(path).absolute()
    with _file_scope() as files:
        key = ("yaml", path)

        def read():
            try:
                content = path.read_bytes()
            except FileNotFoundError:
                return _YamlSnapshot(None, {})
            except OSError as exc:
                raise ValueError(f"Cannot read configuration file: {path}") from exc
            previous = files.seeds.get(key)
            if previous is not None and previous.content == content:
                return previous
            try:
                value = yaml.safe_load(content)
            except yaml.YAMLError as exc:
                raise ValueError(f"Invalid configuration file: {path}") from exc
            if value is None:
                value = {}
            if not isinstance(value, dict):
                raise ValueError(f"Configuration must be a mapping: {path}")
            return _YamlSnapshot(content, value)

        return files.get(key, read)


def _load_project_yaml(project: str) -> dict:
    cache = _scalar_cache
    return _cached_project_yaml(cache, cache.epoch, project, default_config_path(project))


def _cached_project_yaml(cache: _ScalarCache, epoch: int, project: str, path: Path) -> dict:
    path = Path(path).absolute()
    with _file_scope() as files:
        if files.binding is not cache or files.epoch != epoch:
            raise RuntimeError("Picker file configuration is not ready; generation superseded")
        return deepcopy(files.get(("project", project, path), lambda: _read_yaml(path)))


def _load_repo_yaml(project: str) -> dict:
    cache = _scalar_cache
    return _cached_repo_yaml(cache, cache.epoch, project, _repo_dir(project).strip())


def _cached_repo_yaml(cache: _ScalarCache, epoch: int, project: str, repo_dir: str) -> dict:
    repo_dir = str(Path(repo_dir).absolute()) if repo_dir else ""
    with _file_scope() as files:
        if files.binding is not cache or files.epoch != epoch:
            raise RuntimeError("Picker file configuration is not ready; generation superseded")

        def read():
            if repo_dir:
                root = Path(repo_dir)
                for candidate in (root / ".agent-worktrees" / "config.yaml", root / "config.yaml"):
                    snapshot = _yaml_snapshot(candidate)
                    if snapshot.content is not None:
                        return snapshot.value
            return {}

        return deepcopy(files.get(("repo", project, repo_dir), read))


_cached_project_yaml.cache_clear = _invalidate_file_cache
_cached_repo_yaml.cache_clear = _invalidate_file_cache


def _parse_machines_yaml_file(path: Path) -> dict[str, MachineEntry]:
    """Parse one ``machines.yaml`` file's ``machines:`` block into entries.

    Delegates to the shared ``machine_transport`` parser with
    ``require_alias=False`` (this package's historical behavior: keep an
    alias-less SSH environment entry, with ``alias=""``, so the Picker can
    render it as a disabled tab rather than silently omitting it). A
    read/parse failure (missing file, OS error, malformed YAML) is folded
    into the same ``ValueError`` this function has always raised for an
    unusable file -- preserving every existing
    ``except (FileNotFoundError, ValueError)`` call site's behavior exactly,
    rather than letting a new exception type (``yaml.YAMLError``) escape
    uncaught.
    """
    try:
        return _mt_parse_machines_yaml_file(path, require_alias=False)
    except (OSError, yaml.YAMLError) as exc:
        raise ValueError(f"machines.yaml at {path} is missing 'machines' key") from exc


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
    two files are meant to carry disjoint keys. The merged registry then uses
    the shared read-only logical-machine projection; generated key/alias
    collisions refuse rather than overwrite another entry.
    """
    repo_dir = str(Path(repo_dir).absolute())
    try:
        project = project_name()
    except RuntimeError:
        project = None
    with _file_scope() as files:
        def read():
            def document(path):
                snapshot = _yaml_snapshot(path)
                return deepcopy(snapshot.value) if snapshot.content is not None else None

            return machine_roster.read_entries(
                Path(repo_dir), read=document, supplier=lambda: machines_yaml_path(repo_dir),
            )

        return deepcopy(files.get(("machines", project, repo_dir), read))


_load_machines_yaml.cache_clear = _invalidate_file_cache


def load_config() -> ProjectConfig:
    project = project_name()
    with _file_scope() as files:
        def read():
            project_yaml = _load_project_yaml(project)
            repo_yaml = _load_repo_yaml(project)
            repo_name = str(project_yaml.get("repo_name") or project).strip() or project
            machine = _machine_name(project).strip()
            repo_dir = _repo_dir(project).strip()
            default_branch = str(repo_yaml.get("default_branch") or "").strip()
            if not default_branch:
                repos = _read_yaml(harness_state._registry_path(None, "repos")).get("repos", {})
                if not isinstance(repos, dict):
                    raise ValueError("Repository registry entries must be a mapping")
                entry = repos.get(project)
                if isinstance(entry, dict):
                    default_branch = str(entry.get("default_branch") or "").strip()
                elif entry is not None:
                    raise ValueError("Selected repository entry must be a mapping")
            return ProjectConfig(
                repo_name=repo_name, machine=machine,
                default_repo=RepoConfig(anchor=repo_dir, default_branch=default_branch),
                configured_machine=files.binding.machine_bindings.get(project),
                machine_binding_known=project in files.binding.machine_bindings,
            )
        return files.get(("config", project), read)


def load_machines_yaml(repo_dir: str | Path) -> dict[str, MachineEntry]:
    return _load_machines_yaml(str(repo_dir))


def machines_yaml_path(repo_dir: str | Path) -> Path:
    with _file_scope():
        root = Path(repo_dir)
        project = project_name()
        for candidate in (root / ".agent-worktrees" / "machines.yaml", root / "machines.yaml"):
            if _yaml_snapshot(candidate).content is not None:
                return candidate
        knowledge_repo = str(_load_project_yaml(project).get("knowledge_repo") or "").strip()
        if knowledge_repo:
            selection = harness_state.project_anchor_selection(knowledge_repo, reader=_read_yaml)
            overlay_root = Path(selection.anchor) if selection.anchor else None
        else:
            overlay_root = None
        if overlay_root is not None:
            for candidate in (
                overlay_root / ".agent-worktrees" / "machines.yaml",
                overlay_root / "machines.yaml",
            ):
                if _yaml_snapshot(candidate).content is not None:
                    return candidate
        return root / ".agent-worktrees" / "machines.yaml"
