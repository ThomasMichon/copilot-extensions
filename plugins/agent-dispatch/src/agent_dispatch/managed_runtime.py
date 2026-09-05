"""Dispatch-owned materialization of immutable plugin companion runtimes."""

from __future__ import annotations

import hashlib
import json
import logging
import os
import shutil
import stat
import subprocess
import sys
import sysconfig
import time
import uuid
from collections.abc import Callable, Mapping, Sequence
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Protocol

from plugin_activation import read_json_object

from .procutil import no_window_kwargs
from .registrar_reconcile import DECLARED_ID_PREFIX
from .registrations import RegistrationKind
from .single_instance import SingleInstance

RECEIPT_NAME = ".agent-dispatch-managed-runtime.json"
RECEIPT_SCHEMA_VERSION = 2
MANAGED_RUNTIME_ROOT_ENV = "AGENT_DISPATCH_MANAGED_RUNTIME_ROOT"
_LOCK_WAIT_SECONDS = 120.0
_LOCK_POLL_SECONDS = 0.1
_WINDOWS_REPARSE_POINT = 0x400
_COMMAND_TIMEOUT_SECONDS = 600.0
_TRUST_TIMEOUT_SECONDS = 30.0

log = logging.getLogger("agent-dispatch.managed-runtime")


class ManagedRuntimeError(RuntimeError):
    """Managed runtime intent cannot be safely materialized."""


class ManagedRuntimeLockTimeout(ManagedRuntimeError):
    """The shared root is busy; no runtime metadata was inspected or mutated."""


Runner = Callable[[Sequence[str], Path | None, Mapping[str, str]], None]
TrustVerifier = Callable[[Path], bool]


@dataclass(frozen=True)
class ManagedRuntimePolicy:
    """Supervisor-owned physical placement and executable authority."""

    root: Path
    base_python: Path
    package_manager: Path
    windows: bool
    environment: Mapping[str, str]
    base_runtime_paths: tuple[Path, ...] = ()

    @classmethod
    def resolve(cls) -> ManagedRuntimePolicy:
        """Resolve the default policy from the running dispatch installation."""
        root = managed_runtime_root()
        base_python = Path(
            getattr(sys, "_base_executable", None) or sys.executable
        ).resolve(strict=True)
        package_manager_raw = shutil.which("uv")
        if not package_manager_raw:
            raise ManagedRuntimeError(
                "dispatch managed runtimes require the supervisor's uv executable"
            )
        package_manager = Path(package_manager_raw).resolve(strict=True)
        runtime_paths: list[Path] = []
        if os.name != "nt":
            stdlib = Path(sysconfig.get_path("stdlib")).resolve(strict=True)
            runtime_paths.append(stdlib)
            libdir = sysconfig.get_config_var("LIBDIR")
            library = sysconfig.get_config_var("LDLIBRARY")
            if libdir and library:
                candidate = (Path(libdir) / library)
                if candidate.exists():
                    runtime_paths.append(candidate.resolve(strict=True))
        return cls(
            root=root,
            base_python=base_python,
            package_manager=package_manager,
            windows=os.name == "nt",
            environment=_subprocess_environment(
                base_python=base_python,
                package_manager=package_manager,
            ),
            base_runtime_paths=tuple(dict.fromkeys(runtime_paths)),
        )


@dataclass(frozen=True)
class MaterializedRuntime:
    """One validated immutable runtime cell."""

    name: str
    version: str
    profile: str
    content_digest: str
    cell: Path
    python: Path
    receipt: Path


class RuntimeMaterializer(Protocol):
    """The supervisor's preparation and non-provisioning validation boundary."""

    def materialize(
        self, registration: Mapping[str, Any]
    ) -> tuple[MaterializedRuntime, ...]: ...

    def validate(
        self, registration: Mapping[str, Any], runtimes: tuple[MaterializedRuntime, ...]
    ) -> None: ...


def managed_runtime_root() -> Path:
    """Resolve placement without acquiring a package-manager toolchain."""
    return Path(
        os.environ.get(MANAGED_RUNTIME_ROOT_ENV)
        or (Path.home() / ".agent-dispatch" / "managed-runtimes")
    )


def _subprocess_environment(
    *, base_python: Path, package_manager: Path
) -> dict[str, str]:
    """Build a bounded environment without ambient package-manager authority."""
    allowed = {
        "COMSPEC",
        "HOME",
        "HOMEDRIVE",
        "HOMEPATH",
        "LANG",
        "LC_ALL",
        "LOCALAPPDATA",
        "SYSTEMDRIVE",
        "SYSTEMROOT",
        "TEMP",
        "TMP",
        "USERPROFILE",
        "WINDIR",
    }
    environment = {
        key: value
        for key, value in os.environ.items()
        if key.upper() in allowed
    }
    path_entries = {
        str(base_python.parent),
        str(package_manager.parent),
    }
    if os.name == "nt":
        system_root = environment.get("SYSTEMROOT") or environment.get("WINDIR")
        if system_root:
            path_entries.add(str(Path(system_root) / "System32"))
    environment["PATH"] = os.pathsep.join(sorted(path_entries))
    environment["PYTHONDONTWRITEBYTECODE"] = "1"
    environment["PYTHONNOUSERSITE"] = "1"
    environment["PIP_CONFIG_FILE"] = os.devnull
    environment["UV_NO_CONFIG"] = "1"
    return environment


def _default_runner(
    argv: Sequence[str], cwd: Path | None, environment: Mapping[str, str]
) -> None:
    try:
        subprocess.run(  # noqa: S603 -- argv is assembled from trusted policy + snapshots
            list(argv),
            cwd=str(cwd) if cwd else None,
            env=dict(environment),
            check=True,
            capture_output=True,
            stdin=subprocess.DEVNULL,
            text=True,
            timeout=_COMMAND_TIMEOUT_SECONDS,
            **no_window_kwargs(),
        )
    except subprocess.TimeoutExpired as exc:
        raise ManagedRuntimeError(
            f"managed runtime command timed out after {_COMMAND_TIMEOUT_SECONDS:g}s"
        ) from exc
    except subprocess.CalledProcessError as exc:
        detail = (exc.stderr or exc.stdout or "").strip()
        raise ManagedRuntimeError(
            f"managed runtime command failed: {detail or exc.returncode}"
        ) from exc


def _powershell_path() -> Path | None:
    system_root = os.environ.get("SYSTEMROOT") or os.environ.get("WINDIR")
    if system_root:
        candidate = (
            Path(system_root)
            / "System32"
            / "WindowsPowerShell"
            / "v1.0"
            / "powershell.exe"
        )
        if candidate.is_file():
            return candidate
    candidate_raw = shutil.which("powershell.exe") or shutil.which("pwsh.exe")
    return Path(candidate_raw).resolve() if candidate_raw else None


def _authenticode_valid(path: Path) -> bool:
    """Verify a Windows executable through the OS trust provider."""
    if os.name != "nt":
        return True
    powershell = _powershell_path()
    if powershell is None:
        return False
    escaped = str(path).replace("'", "''")
    command = (
        "$s=Get-AuthenticodeSignature -LiteralPath '"
        + escaped
        + "'; if($s.Status -eq 'Valid'){exit 0}else{exit 1}"
    )
    try:
        result = subprocess.run(  # noqa: S603 -- fixed trusted system executable
            [
                str(powershell),
                "-NoLogo",
                "-NoProfile",
                "-NonInteractive",
                "-Command",
                command,
            ],
            check=False,
            stdin=subprocess.DEVNULL,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.DEVNULL,
            timeout=_TRUST_TIMEOUT_SECONDS,
            **no_window_kwargs(),
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    return result.returncode == 0


def _is_reparse(path: Path) -> bool:
    try:
        info = path.lstat()
    except OSError:
        return True
    return bool(
        getattr(info, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    )


def _reject_link(path: Path, *, description: str) -> None:
    try:
        info = path.lstat()
    except FileNotFoundError:
        return
    if stat.S_ISLNK(info.st_mode) or (
        getattr(info, "st_file_attributes", 0) & _WINDOWS_REPARSE_POINT
        or getattr(info, "st_reparse_tag", 0)
    ):
        raise ManagedRuntimeError(f"{description} must not be a link or reparse point")


def _ensure_safe_root(root: Path) -> Path:
    root = root.expanduser().absolute()
    for ancestor in (root, *root.parents):
        _reject_link(ancestor, description="managed runtime root ancestor")
    existing = root
    missing: list[Path] = []
    while not existing.exists():
        missing.append(existing)
        if existing.parent == existing:
            break
        existing = existing.parent
    for path in reversed(missing):
        path.mkdir(exist_ok=True)
        _reject_link(path, description="managed runtime root")
    if not root.is_dir():
        raise ManagedRuntimeError("managed runtime root must be a directory")
    _reject_link(root, description="managed runtime root")
    return root


def _assert_safe_descendant(root: Path, path: Path, *, description: str) -> None:
    try:
        relative = path.relative_to(root)
    except ValueError as exc:
        raise ManagedRuntimeError(f"{description} escapes the managed runtime root") from exc
    if ".." in relative.parts:
        raise ManagedRuntimeError(f"{description} escapes the managed runtime root")
    for ancestor in root.parents:
        _reject_link(ancestor, description=description)
    current = root
    _reject_link(current, description=description)
    for component in relative.parts:
        current = current / component
        if current.exists() or current.is_symlink():
            _reject_link(current, description=description)


def _safe_directory(root: Path, *components: str) -> Path:
    current = root
    for component in components:
        candidate = current / component
        _assert_safe_descendant(root, candidate, description="managed runtime directory")
        if candidate.exists():
            if not candidate.is_dir():
                raise ManagedRuntimeError(
                    "managed runtime directory component is not a directory"
                )
        else:
            candidate.mkdir()
        _reject_link(candidate, description="managed runtime directory")
        current = candidate
    return current


def _quarantine_cell(root: Path, cell: Path) -> Path:
    _assert_safe_descendant(root, cell, description="managed runtime cell")
    failed_root = _safe_directory(root, ".failed")
    target = failed_root / uuid.uuid4().hex
    os.replace(cell, target)
    return target


class _RootLock:
    def __init__(
        self,
        root: Path,
        *,
        clock: Callable[[], float] = time.monotonic,
        sleep: Callable[[float], None] = time.sleep,
        timeout: float = _LOCK_WAIT_SECONDS,
    ):
        self._root = root
        self._lock = SingleInstance(root / ".materialize.lock")
        self._clock = clock
        self._sleep = sleep
        self._timeout = timeout

    def __enter__(self) -> _RootLock:
        _assert_safe_descendant(
            self._root, self._lock.lock_path, description="managed runtime root lock"
        )
        if self._lock.lock_path.exists() and (
            not self._lock.lock_path.is_file() or self._lock.lock_path.stat().st_nlink != 1
        ):
            raise ManagedRuntimeError("managed runtime root lock must be an unlinked regular file")
        deadline = self._clock() + self._timeout
        while not self._lock.acquire():
            if self._clock() >= deadline:
                raise ManagedRuntimeLockTimeout(
                    "timed out waiting for the managed runtime root lock"
                )
            self._sleep(_LOCK_POLL_SECONDS)
        return self

    def __exit__(self, *exc: object) -> None:
        self._lock.release()


def _canonical_digest(value: object) -> str:
    payload = json.dumps(
        value, sort_keys=True, separators=(",", ":"), ensure_ascii=True
    ).encode("utf-8")
    return hashlib.sha256(payload).hexdigest()


def _plugin_identity(authority: Mapping[str, Any]) -> str:
    return _canonical_digest(
        {
            "owner": authority["plugin_owner"],
            "root": authority["plugin_root"],
            "source": authority["plugin_source_path"],
        }
    )[:16]


def _cell_key(receipt: Mapping[str, Any]) -> str:
    identity = {
        key: receipt[key]
        for key in (
            "name", "version", "profile", "content_digest", "authority_digest", "toolchain_digest"
        )
    }
    if receipt["schema_version"] == RECEIPT_SCHEMA_VERSION:
        identity["schema_version"] = RECEIPT_SCHEMA_VERSION
    return _canonical_digest(identity)[:40]


def _read_metadata(root: Path, path: Path) -> dict[str, Any]:
    """Read mandatory, ordinary metadata without accepting duplicate keys or races."""
    _assert_safe_descendant(root, path, description="managed runtime metadata")
    try:
        before = path.stat(follow_symlinks=False)
        if not stat.S_ISREG(before.st_mode) or before.st_nlink != 1 or before.st_size > 4 * 1024 * 1024:
            raise ManagedRuntimeError("managed runtime metadata must be a bounded regular file")
        digest = _hash_regular_file(path, description="managed runtime metadata")
        _, value = read_json_object(path)
        after = path.stat(follow_symlinks=False)
        if (
            (before.st_dev, before.st_ino, before.st_size, before.st_mtime_ns)
            != (after.st_dev, after.st_ino, after.st_size, after.st_mtime_ns)
            or digest != _hash_regular_file(path, description="managed runtime metadata")
        ):
            raise ManagedRuntimeError("managed runtime metadata changed while being read")
        return value
    except (OSError, ValueError) as exc:
        raise ManagedRuntimeError(f"managed runtime metadata is unavailable or invalid: {path}") from exc


def _walk_error(error: OSError) -> None:
    raise error


def _python_path(environment: Path, *, windows: bool) -> Path:
    return environment / ("python.exe" if windows else "bin/python")


def _authority(
    registration: Mapping[str, Any], *, require_payload: bool = True
) -> tuple[dict[str, Any], Path]:
    if (
        registration.get("kind") != RegistrationKind.PLUGIN_COMPANION
        or registration.get("source") != DECLARED_ID_PREFIX
    ):
        raise ManagedRuntimeError(
            "managed runtimes require an attributed plugin declaration"
        )
    plugin = registration.get("plugin")
    revision = registration.get("runtime_revision")
    spec = registration.get("spec")
    if (
        not isinstance(plugin, dict)
        or not isinstance(revision, dict)
        or not isinstance(spec, dict)
    ):
        raise ManagedRuntimeError("managed runtime registration provenance is incomplete")
    managed = spec.get("managed_runtime")
    if not isinstance(managed, dict) or managed != revision.get("managed_runtime"):
        raise ManagedRuntimeError("managed runtime declaration authority is inconsistent")
    expected = {
        "plugin_root": plugin.get("root"),
        "plugin_owner": registration.get("owner"),
        "plugin_source_path": plugin.get("source_path"),
        "plugin_version": plugin.get("version"),
        "activation_scopes": plugin.get("activation_scopes"),
        "managed_runtime": managed,
    }
    if revision != expected:
        raise ManagedRuntimeError("managed runtime provenance does not match its declaration")
    plugin_root_raw = plugin.get("root")
    if not isinstance(plugin_root_raw, str):
        raise ManagedRuntimeError("managed runtime plugin root is missing")
    plugin_root = Path(plugin_root_raw).absolute()
    if require_payload:
        if not plugin_root.is_dir():
            raise ManagedRuntimeError("managed runtime plugin root is unavailable")
        _reject_link(plugin_root, description="managed runtime plugin root")
    return expected, plugin_root


def _contained_path(plugin_root: Path, relative: str) -> Path:
    if (
        not relative
        or "\\" in relative
        or Path(relative).is_absolute()
        or (relative != "." and any(part in {"", ".", ".."} for part in relative.split("/")))
    ):
        raise ManagedRuntimeError(
            "managed runtime project must be a contained plugin-relative path"
        )
    target = plugin_root if relative == "." else plugin_root.joinpath(*relative.split("/"))
    current = plugin_root
    for component in (() if relative == "." else relative.split("/")):
        current = current / component
        _reject_link(current, description="managed runtime project path")
    try:
        target.relative_to(plugin_root)
    except ValueError as exc:
        raise ManagedRuntimeError(
            "managed runtime project escapes the attributed plugin root"
        ) from exc
    if not target.exists():
        raise ManagedRuntimeError("managed runtime project path is unavailable")
    return target


def _contained_project(plugin_root: Path, relative: str) -> Path:
    target = _contained_path(plugin_root, relative)
    if not target.is_dir():
        raise ManagedRuntimeError("managed runtime project must be a directory")
    return target


def _copy_file(source: Path, destination: Path) -> tuple[int, str]:
    before = source.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ManagedRuntimeError("managed runtime snapshots accept regular files only")
    digest = hashlib.sha256()
    destination.parent.mkdir(parents=True, exist_ok=True)
    with source.open("rb") as reader, destination.open("xb") as writer:
        opened = os.fstat(reader.fileno())
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            raise ManagedRuntimeError("managed runtime source changed during snapshot")
        while chunk := reader.read(1024 * 1024):
            digest.update(chunk)
            writer.write(chunk)
        writer.flush()
        os.fsync(writer.fileno())
    os.chmod(destination, stat.S_IMODE(before.st_mode))
    after = source.stat(follow_symlinks=False)
    if (
        after.st_dev != before.st_dev
        or after.st_ino != before.st_ino
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
        or stat.S_IMODE(after.st_mode) != stat.S_IMODE(before.st_mode)
    ):
        raise ManagedRuntimeError("managed runtime source changed during snapshot")
    return before.st_size, digest.hexdigest()


def _hash_regular_file(path: Path, *, description: str) -> str:
    _reject_link(path, description=description)
    before = path.stat(follow_symlinks=False)
    if not stat.S_ISREG(before.st_mode):
        raise ManagedRuntimeError(f"{description} must be a regular file")
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        opened = os.fstat(stream.fileno())
        if (
            opened.st_dev != before.st_dev
            or opened.st_ino != before.st_ino
            or opened.st_size != before.st_size
        ):
            raise ManagedRuntimeError(f"{description} changed while hashing")
        while chunk := stream.read(1024 * 1024):
            digest.update(chunk)
    after = path.stat(follow_symlinks=False)
    if (
        after.st_dev != before.st_dev
        or after.st_ino != before.st_ino
        or after.st_size != before.st_size
        or after.st_mtime_ns != before.st_mtime_ns
    ):
        raise ManagedRuntimeError(f"{description} changed while hashing")
    return digest.hexdigest()


def _copy_project(
    source: Path,
    destination: Path,
    *,
    prefix: str,
    excluded: frozenset[str] = frozenset(),
    excluded_suffixes: tuple[str, ...] = (),
) -> list[dict[str, Any]]:
    manifest: list[dict[str, Any]] = []
    destination.mkdir(parents=True)
    source_mode = stat.S_IMODE(source.stat(follow_symlinks=False).st_mode)
    os.chmod(destination, source_mode)
    manifest.append({"path": prefix, "type": "directory", "mode": source_mode})
    for current, directories, files in os.walk(source, topdown=True, followlinks=False):
        current_path = Path(current)
        _reject_link(current_path, description="managed runtime project directory")
        relative = current_path.relative_to(source)
        directories.sort()
        files.sort()
        for name in list(directories):
            child = current_path / name
            relative_child = relative / name
            if any(component in excluded for component in relative_child.parts):
                directories.remove(name)
                continue
            _reject_link(child, description="managed runtime project directory")
            mode = child.stat(follow_symlinks=False).st_mode
            if not stat.S_ISDIR(mode):
                raise ManagedRuntimeError(
                    "managed runtime snapshots accept directories and regular files only"
                )
            (destination / relative / name).mkdir()
            child_mode = stat.S_IMODE(mode)
            os.chmod(destination / relative / name, child_mode)
            manifest.append(
                {
                    "path": f"{prefix}/{relative_child.as_posix()}",
                    "type": "directory",
                    "mode": child_mode,
                }
            )
        for name in files:
            child = current_path / name
            relative_file = relative / name
            if (
                any(component in excluded for component in relative_file.parts)
                or name.endswith(excluded_suffixes)
            ):
                continue
            _reject_link(child, description="managed runtime project file")
            size, digest = _copy_file(child, destination / relative_file)
            manifest.append(
                {
                    "path": f"{prefix}/{relative_file.as_posix()}",
                    "type": "file",
                    "mode": stat.S_IMODE(
                        child.stat(follow_symlinks=False).st_mode
                    ),
                    "size": size,
                    "sha256": digest,
                }
            )
    return manifest


def _windows_runtime_sources(base_python: Path) -> tuple[list[Path], list[Path]]:
    base = base_python.parent
    versioned_dll = f"python{sys.version_info.major}{sys.version_info.minor}.dll"
    required_files = [base_python, base / versioned_dll]
    optional_patterns = (
        "python3.dll",
        "pythonw.exe",
        "python*.zip",
        "vcruntime*.dll",
        "msvcp*.dll",
    )
    files = list(required_files)
    for pattern in optional_patterns:
        files.extend(sorted(base.glob(pattern)))
    unique_files = list(dict.fromkeys(files))
    missing = [path for path in required_files if not path.is_file()]
    if missing:
        raise ManagedRuntimeError(
            "trusted Windows base Python is missing required runtime files: "
            + ", ".join(path.name for path in missing)
        )
    directories = [path for path in (base / "Lib", base / "DLLs", base / "tcl") if path.is_dir()]
    if base / "Lib" not in directories:
        raise ManagedRuntimeError("trusted Windows base Python is missing its standard library")
    return unique_files, directories


def _windows_runtime_digest(base_python: Path) -> str:
    files, directories = _windows_runtime_sources(base_python)
    digest = hashlib.sha256()
    base = base_python.parent
    for path in files:
        digest.update(path.relative_to(base).as_posix().encode("utf-8"))
        digest.update(
            _hash_regular_file(
                path, description="Windows base Python runtime file"
            ).encode("ascii")
        )
    for directory in directories:
        for current, child_dirs, child_files in os.walk(
            directory, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            _reject_link(current_path, description="Windows base Python runtime directory")
            relative = current_path.relative_to(base).as_posix()
            if relative == "Lib":
                child_dirs[:] = [
                    name
                    for name in child_dirs
                    if name not in {"site-packages", "__pycache__"}
                ]
            else:
                child_dirs[:] = [name for name in child_dirs if name != "__pycache__"]
            child_dirs.sort()
            child_files.sort()
            for name in child_files:
                if name.endswith((".pyc", ".pyo")):
                    continue
                path = current_path / name
                digest.update(path.relative_to(base).as_posix().encode("utf-8"))
                digest.update(
                    _hash_regular_file(
                        path, description="Windows base Python runtime file"
                    ).encode("ascii")
                )
    return digest.hexdigest()


def _copy_windows_runtime(
    base_python: Path, destination: Path, *, trust_verifier: TrustVerifier
) -> Path:
    files, directories = _windows_runtime_sources(base_python)
    base = base_python.parent
    destination.mkdir()
    for source in files:
        relative = source.relative_to(base)
        _copy_file(source, destination / relative)
    for source in directories:
        excluded = (
            frozenset({"site-packages", "__pycache__"})
            if source.name == "Lib"
            else frozenset({"__pycache__"})
        )
        _copy_project(
            source,
            destination / source.name,
            prefix=source.name,
            excluded=excluded,
            excluded_suffixes=(".pyc", ".pyo"),
        )
    copied_python = destination / base_python.name
    _verify_windows_runtime_trust(
        destination,
        _windows_trust_files(base_python),
        trust_verifier,
    )
    return copied_python


def _windows_trust_files(base_python: Path) -> tuple[str, ...]:
    files, directories = _windows_runtime_sources(base_python)
    base = base_python.parent
    trust_files = [
        path.relative_to(base).as_posix()
        for path in files
        if path.suffix.casefold() in {".exe", ".dll"}
    ]
    for directory in directories:
        for current, child_dirs, child_files in os.walk(
            directory, topdown=True, followlinks=False
        ):
            current_path = Path(current)
            relative = current_path.relative_to(base).as_posix()
            if relative == "Lib":
                child_dirs[:] = [
                    name
                    for name in child_dirs
                    if name not in {"site-packages", "__pycache__"}
                ]
            else:
                child_dirs[:] = [
                    name for name in child_dirs if name != "__pycache__"
                ]
            child_dirs.sort()
            child_files.sort()
            trust_files.extend(
                (current_path / name).relative_to(base).as_posix()
                for name in child_files
                if not name.endswith((".pyc", ".pyo"))
                and Path(name).suffix.casefold() in {".exe", ".dll"}
            )
    return tuple(sorted(set(trust_files), key=str.casefold))


def _verify_windows_runtime_trust(
    runtime_root: Path,
    trust_files: Sequence[str],
    trust_verifier: TrustVerifier,
) -> None:
    paths = [runtime_root.joinpath(*relative.split("/")) for relative in trust_files]
    if not paths or any(
        not path.is_file()
        or path.is_symlink()
        or _is_reparse(path)
        or not trust_verifier(path)
        for path in paths
    ):
        raise ManagedRuntimeError(
            "managed runtime copied Windows Python files failed trust verification"
        )


def _tree_digest(root: Path, *, excluded: frozenset[str] = frozenset()) -> str:
    digest = hashlib.sha256()
    for current, directories, files in os.walk(
        root, topdown=True, followlinks=False, onerror=_walk_error
    ):
        current_path = Path(current)
        _reject_link(current_path, description="managed runtime cell directory")
        directories.sort()
        files.sort()
        for name in directories:
            child = current_path / name
            _reject_link(child, description="managed runtime cell directory")
            if not stat.S_ISDIR(child.stat(follow_symlinks=False).st_mode):
                raise ManagedRuntimeError(
                    "managed runtime cell contains a non-directory entry"
                )
            relative = child.relative_to(root).as_posix()
            mode = stat.S_IMODE(child.stat(follow_symlinks=False).st_mode)
            digest.update(f"D\0{relative}\0{mode:o}\0".encode())
        for name in files:
            child = current_path / name
            relative = child.relative_to(root).as_posix()
            if relative in excluded:
                continue
            _reject_link(child, description="managed runtime cell file")
            if not stat.S_ISREG(child.stat(follow_symlinks=False).st_mode):
                raise ManagedRuntimeError(
                    "managed runtime cell contains a special file"
                )
            digest.update(relative.encode("utf-8"))
            mode = stat.S_IMODE(child.stat(follow_symlinks=False).st_mode)
            digest.update(f"\0{mode:o}\0".encode("ascii"))
            digest.update(
                _hash_regular_file(
                    child, description="managed runtime cell file"
                ).encode("ascii")
            )
    return digest.hexdigest()


def _runtime_paths_digest(paths: Sequence[Path]) -> str:
    digest = hashlib.sha256()
    for path in paths:
        resolved = path.resolve(strict=True)
        digest.update(str(resolved).encode("utf-8"))
        if resolved.is_dir():
            digest.update(_tree_digest(resolved).encode("ascii"))
        else:
            digest.update(
                _hash_regular_file(
                    resolved, description="dispatch policy base runtime file"
                ).encode("ascii")
            )
    return digest.hexdigest()


class ManagedRuntimeMaterializer:
    """Build or reuse immutable runtime cells under dispatch-owned policy."""

    def __init__(
        self,
        policy: ManagedRuntimePolicy | None = None,
        *,
        runner: Runner = _default_runner,
        trust_verifier: TrustVerifier = _authenticode_valid,
        lock_factory: Callable[[Path], Any] = _RootLock,
    ):
        self.policy = policy
        self.runner = runner
        self.trust_verifier = trust_verifier
        self.lock_factory = lock_factory

    def _policy(self) -> ManagedRuntimePolicy:
        if self.policy is None:
            self.policy = ManagedRuntimePolicy.resolve()
        return self.policy

    def _toolchain_digest(self, policy: ManagedRuntimePolicy) -> str:
        if not policy.windows and not policy.base_runtime_paths:
            raise ManagedRuntimeError(
                "dispatch policy must identify the POSIX base Python runtime"
            )
        return _canonical_digest(
            {
                "base_python": str(policy.base_python),
                "base_python_digest": (
                    _windows_runtime_digest(policy.base_python)
                    if policy.windows
                    else _hash_regular_file(
                        policy.base_python,
                        description="dispatch policy base Python",
                    )
                ),
                "package_manager": str(policy.package_manager),
                "package_manager_digest": _hash_regular_file(
                    policy.package_manager,
                    description="dispatch policy package manager",
                ),
                "base_runtime_digest": (
                    None
                    if policy.windows
                    else _runtime_paths_digest(policy.base_runtime_paths)
                ),
                "windows": policy.windows,
            }
        )

    def _validate_imports(
        self,
        python: Path,
        imports: Sequence[str],
        *,
        cwd: Path,
        environment: Mapping[str, str],
    ) -> None:
        code = (
            "import importlib\n"
            f"names={json.dumps(list(imports), ensure_ascii=True)}\n"
            "for name in names: importlib.import_module(name)\n"
        )
        self.runner([str(python), "-I", "-B", "-c", code], cwd, environment)

    def _ready(
        self,
        cell: Path,
        expected: Mapping[str, Any],
        *,
        root: Path,
        policy: ManagedRuntimePolicy,
    ) -> MaterializedRuntime | None:
        _assert_safe_descendant(root, cell, description="managed runtime cell")
        receipt_path = cell / RECEIPT_NAME
        python = _python_path(cell / "runtime", windows=policy.windows)
        _assert_safe_descendant(root, receipt_path, description="managed runtime receipt")
        _assert_safe_descendant(root, python, description="managed runtime Python")
        receipt = _read_metadata(root, receipt_path)
        recorded_cell_digest = receipt.pop("cell_digest", None)
        if (
            receipt != expected
            or not isinstance(recorded_cell_digest, str)
            or _tree_digest(cell, excluded=frozenset({RECEIPT_NAME}))
            != recorded_cell_digest
            or not python.is_file()
        ):
            return None
        if policy.windows:
            _verify_windows_runtime_trust(
                cell / "runtime",
                expected["windows_trust_files"],
                self.trust_verifier,
            )
        self._validate_imports(
            python,
            expected["imports"],
            cwd=cell,
            environment=policy.environment,
        )
        if (
            _tree_digest(cell, excluded=frozenset({RECEIPT_NAME}))
            != recorded_cell_digest
        ):
            return None
        if policy.windows:
            _verify_windows_runtime_trust(
                cell / "runtime",
                expected["windows_trust_files"],
                self.trust_verifier,
            )
        return MaterializedRuntime(
            name=expected["name"],
            version=expected["version"],
            profile=expected["profile"],
            content_digest=expected["content_digest"],
            cell=cell,
            python=python,
            receipt=receipt_path,
        )

    def _materialize_one(
        self,
        *,
        root: Path,
        plugin_root: Path,
        plugin_identity: str,
        authority: Mapping[str, Any],
        authority_digest: str,
        runtime: Mapping[str, Any],
        policy: ManagedRuntimePolicy,
        toolchain_digest: str,
    ) -> MaterializedRuntime:
        staging_parent = _safe_directory(root, ".staging")
        staging = staging_parent / uuid.uuid4().hex
        staging.mkdir()
        try:
            snapshot_root = staging / "snapshot"
            projects_root = snapshot_root / "projects"
            projects_root.mkdir(parents=True)
            manifest: list[dict[str, Any]] = []
            project_manifest: list[dict[str, Any]] = []
            project_receipts: list[dict[str, Any]] = []
            for index, project in enumerate(runtime["projects"]):
                project_path = str(project["path"])
                source = _contained_project(plugin_root, project_path)
                destination = projects_root / f"{index:03d}"
                project_manifest.extend(
                    _copy_project(source, destination, prefix=f"projects/{index:03d}")
                )
                extras = list(project.get("extras") or [])
                project_receipts.append({"path": project_path, "extras": extras})
            identity_paths = runtime.get("identity_paths")
            if identity_paths:
                identity_receipts: list[str] = []
                identity_root = snapshot_root / "identity"
                identity_root.mkdir(parents=True, exist_ok=True)
                for index, identity_path in enumerate(identity_paths):
                    relative = str(identity_path)
                    source = _contained_path(plugin_root, relative)
                    destination = identity_root / f"{index:03d}"
                    prefix = f"identity/{index:03d}"
                    if source.is_dir():
                        manifest.extend(_copy_project(source, destination, prefix=prefix))
                    else:
                        destination.mkdir(parents=True, exist_ok=True)
                        size, digest = _copy_file(source, destination / source.name)
                        manifest.append(
                            {
                                "path": f"{prefix}/{source.name}",
                                "type": "file",
                                "mode": stat.S_IMODE(
                                    source.stat(follow_symlinks=False).st_mode
                                ),
                                "size": size,
                                "sha256": digest,
                            }
                        )
                    identity_receipts.append(relative)
            else:
                manifest = project_manifest
            manifest.sort(key=lambda entry: entry["path"])
            snapshot = {
                "projects": project_receipts,
                "files": manifest,
            }
            if identity_paths:
                snapshot["identity_paths"] = identity_receipts
            content_digest = _canonical_digest(
                {
                    "runtime": {
                        key: runtime[key]
                        for key in (
                            "name",
                            "version",
                            "profile",
                            "projects",
                            "imports",
                        )
                    },
                    "snapshot": snapshot,
                }
            )
            cell_key = _cell_key(
                {
                    "schema_version": RECEIPT_SCHEMA_VERSION,
                    "name": runtime["name"],
                    "version": runtime["version"],
                    "profile": runtime["profile"],
                    "content_digest": content_digest,
                    "authority_digest": authority_digest,
                    "toolchain_digest": toolchain_digest,
                }
            )
            cell = (
                root
                / "cells"
                / plugin_identity
                / cell_key
            )
            expected = {
                "schema_version": RECEIPT_SCHEMA_VERSION,
                "name": runtime["name"],
                "version": runtime["version"],
                "profile": runtime["profile"],
                "content_digest": content_digest,
                "authority_digest": authority_digest,
                "toolchain_digest": toolchain_digest,
                "imports": list(runtime["imports"]),
                "windows_trust_files": (
                    list(_windows_trust_files(policy.base_python))
                    if policy.windows
                    else []
                ),
                "snapshot": snapshot,
                "ownership": {
                    "root": str(root),
                    "cell": str(cell),
                    "authority": dict(authority),
                    "windows": policy.windows,
                },
            }
            if cell.exists():
                ready = self._ready(cell, expected, root=root, policy=policy)
                if ready is not None:
                    return ready
                raise ManagedRuntimeError("existing managed runtime cell is invalid; preserving it")

            runtime_root = staging / "runtime"
            if policy.windows:
                if not self.trust_verifier(policy.base_python):
                    raise ManagedRuntimeError(
                        "dispatch policy selected an untrusted Windows base Python"
                    )
                python = _copy_windows_runtime(
                    policy.base_python,
                    runtime_root,
                    trust_verifier=self.trust_verifier,
                )
            else:
                self.runner(
                    [
                        str(policy.base_python),
                        "-I",
                        "-m",
                        "venv",
                        "--copies",
                        str(runtime_root),
                    ],
                    staging,
                    policy.environment,
                )
                python = _python_path(runtime_root, windows=False)
            if not python.is_file():
                raise ManagedRuntimeError(
                    "managed runtime environment did not create a Python executable"
                )
            build_projects = staging / "build-inputs" / "projects"
            build_projects.mkdir(parents=True)
            install_targets: list[str] = []
            for index, project in enumerate(project_receipts):
                source = projects_root / f"{index:03d}"
                destination = build_projects / f"{index:03d}"
                _copy_project(
                    source,
                    destination,
                    prefix=f"projects/{index:03d}",
                )
                extras = project["extras"]
                suffix = f"[{','.join(extras)}]" if extras else ""
                install_targets.append(f"{destination}{suffix}")
            self.runner(
                [
                    str(policy.package_manager),
                    "pip",
                    "install",
                    "--python",
                    str(python),
                    *install_targets,
                ],
                snapshot_root,
                policy.environment,
            )
            shutil.rmtree(staging / "build-inputs")
            before_validation = _tree_digest(staging)
            self._validate_imports(
                python,
                runtime["imports"],
                cwd=staging,
                environment=policy.environment,
            )
            if _tree_digest(staging) != before_validation:
                raise ManagedRuntimeError(
                    "managed runtime import validation modified the staged cell"
                )
            if self._toolchain_digest(policy) != toolchain_digest:
                raise ManagedRuntimeError(
                    "dispatch managed runtime toolchain changed during materialization"
                )
            receipt = dict(expected)
            receipt["cell_digest"] = before_validation
            receipt_path = staging / RECEIPT_NAME
            with receipt_path.open("x", encoding="utf-8", newline="\n") as receipt_file:
                receipt_file.write(
                    json.dumps(receipt, indent=2, sort_keys=True) + "\n"
                )
                receipt_file.flush()
                os.fsync(receipt_file.fileno())
            cell_parent = _safe_directory(
                root,
                "cells",
                plugin_identity,
            )
            if cell.parent != cell_parent:
                raise ManagedRuntimeError("managed runtime publication path is inconsistent")
            _assert_safe_descendant(root, cell_parent, description="managed runtime cell")
            if cell.exists() or cell.is_symlink():
                raise ManagedRuntimeError(
                    f"managed runtime publication destination already exists: {cell}"
                )
            os.replace(staging, cell)
            try:
                ready = self._ready(cell, expected, root=root, policy=policy)
            except ManagedRuntimeError:
                _quarantine_cell(root, cell)
                raise
            if ready is None:
                _quarantine_cell(root, cell)
                raise ManagedRuntimeError(
                    "managed runtime publication failed post-publish validation"
                )
            return ready
        finally:
            if staging.exists():
                primary_error = sys.exc_info()[0] is not None
                try:
                    shutil.rmtree(staging)
                except OSError as exc:
                    if primary_error:
                        log.error(
                            "failed to clean managed runtime staging directory: %s",
                            exc,
                            exc_info=True,
                        )
                    else:
                        raise ManagedRuntimeError(
                            f"failed to clean managed runtime staging directory: {exc}"
                        ) from exc

    def materialize(
        self, registration: Mapping[str, Any]
    ) -> tuple[MaterializedRuntime, ...]:
        """Build or reuse every runtime in one attributed companion declaration."""
        authority, plugin_root = _authority(registration)
        policy = self._policy()
        root = _ensure_safe_root(policy.root)
        if not policy.base_python.is_file() or not policy.package_manager.is_file():
            raise ManagedRuntimeError("dispatch managed runtime toolchain is unavailable")
        authority_digest = _canonical_digest(authority)
        plugin_identity = _plugin_identity(authority)
        managed = authority["managed_runtime"]
        with self.lock_factory(root):
            toolchain_digest = self._toolchain_digest(policy)
            return tuple(
                self._materialize_one(
                    root=root,
                    plugin_root=plugin_root,
                    plugin_identity=plugin_identity,
                    authority=authority,
                    authority_digest=authority_digest,
                    runtime=runtime,
                    policy=policy,
                    toolchain_digest=toolchain_digest,
                )
                for runtime in managed["runtimes"]
            )

    def validate(
        self,
        registration: Mapping[str, Any],
        runtimes: tuple[MaterializedRuntime, ...],
    ) -> None:
        """Revalidate published launch/rollback cells without rebuilding a payload."""
        authority, _ = _authority(registration, require_payload=False)
        policy = self._policy()
        root = policy.root.expanduser().absolute()
        if not root.is_dir():
            raise ManagedRuntimeError("selected managed runtime root is unavailable")
        root = _ensure_safe_root(root)
        declared = authority["managed_runtime"]["runtimes"]
        if len(runtimes) != len(declared):
            raise ManagedRuntimeError("selected managed runtime set is incomplete")
        authority_digest = _canonical_digest(authority)
        plugin_identity = _plugin_identity(authority)
        with self.lock_factory(root):
            toolchain_digest = self._toolchain_digest(policy)
            for runtime, declaration in zip(runtimes, declared):
                expected = _read_metadata(root, runtime.receipt)
                schema = expected.get("schema_version")
                if type(schema) is not int or schema not in (1, RECEIPT_SCHEMA_VERSION):
                    raise ManagedRuntimeError("selected managed runtime receipt version is invalid")
                cell_key = _cell_key(
                    {
                        "schema_version": schema,
                        "name": declaration["name"],
                        "version": declaration["version"],
                        "profile": declaration["profile"],
                        "content_digest": runtime.content_digest,
                        "authority_digest": authority_digest,
                        "toolchain_digest": toolchain_digest,
                    }
                )
                cell = root / "cells" / plugin_identity / cell_key
                if (
                    runtime.cell != cell
                    or runtime.receipt != cell / RECEIPT_NAME
                    or runtime.python != _python_path(cell / "runtime", windows=policy.windows)
                ):
                    raise ManagedRuntimeError("selected managed runtime location is inconsistent")
                expected.pop("cell_digest", None)
                if schema == RECEIPT_SCHEMA_VERSION and expected.get("ownership") != {
                    "root": str(root), "cell": str(cell), "authority": authority,
                    "windows": policy.windows,
                }:
                    raise ManagedRuntimeError("selected managed runtime ownership is inconsistent")
                if any(
                    expected.get(key) != value
                    for key, value in {
                        "schema_version": schema,
                        "name": declaration["name"],
                        "version": declaration["version"],
                        "profile": declaration["profile"],
                        "content_digest": runtime.content_digest,
                        "authority_digest": authority_digest,
                        "toolchain_digest": toolchain_digest,
                        "imports": declaration["imports"],
                        "windows_trust_files": (
                            list(_windows_trust_files(policy.base_python)) if policy.windows else []
                        ),
                    }.items()
                ):
                    raise ManagedRuntimeError("selected managed runtime authority is inconsistent")
                if _canonical_digest(
                    {
                        "runtime": {
                            key: declaration[key]
                            for key in ("name", "version", "profile", "projects", "imports")
                        },
                        "snapshot": expected.get("snapshot"),
                    }
                ) != runtime.content_digest:
                    raise ManagedRuntimeError("selected managed runtime snapshot is inconsistent")
                if self._ready(cell, expected, root=root, policy=policy) != runtime:
                    raise ManagedRuntimeError("selected managed runtime is not ready")
