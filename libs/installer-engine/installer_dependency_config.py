"""Private UV settings-projection and configuration-retention primitives."""
from __future__ import annotations

import configparser
import errno
import hashlib
import json
import math
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time as clock
import types
from collections.abc import Callable, Iterable, Iterator, Mapping, Sequence, Set
from contextlib import contextmanager
from datetime import date, datetime, time
from pathlib import Path, PureWindowsPath
from typing import TYPE_CHECKING, ContextManager, Protocol, TypeVar, TypedDict, cast, overload

if TYPE_CHECKING:
    import ctypes
    from installer_dependency_arguments import (
        captured_environment as captured_environment,
        check_compile_projection as check_compile_projection,
        cutoff_arguments as cutoff_arguments,
        cutoff_value as cutoff_value,
        merge_config as merge_config,
        package_cutoffs as package_cutoffs,
        resolver_configuration as resolver_configuration,
    )

_Path = str | os.PathLike[str]
_Error = type[Exception]
_T = TypeVar("_T")
_U = TypeVar("_U")
_T_co = TypeVar("_T_co", covariant=True)
_PathT = TypeVar("_PathT", bound=_Path)
_Pairs = Callable[[list[str]], Iterable[tuple[str, str]]]
_Balanced = Callable[[str, int, str, str], tuple[str, int]]
_Toml = Callable[[Path], dict[str, object]]
_Mirror = Callable[[Path], Path]
_SourceInputs = tuple[Path, set[Path], set[Path], set[Path]]


class _ArgumentPairs(Protocol):
    def __call__(
        self, arguments: Iterable[str], value_options: Set[str], *, error_type: _Error,
    ) -> Iterator[tuple[str, str]]: ...


class _RawBalanced(Protocol):
    def __call__(
        self, text: str, start: int, opening: str, closing: str, *, error_type: _Error,
    ) -> tuple[str, int]: ...


class _UVCommand(Protocol):
    def __call__(
        self, uv: str, arguments: list[str], *, cwd: Path | None, directory: Path,
        operation: str, windows: bool, argument_pairs: _Pairs, value_options: Set[str],
    ) -> tuple[list[str], Path | None]: ...


class _WindowsLibrary(Protocol):
    def __call__(self, name: str, *, use_last_error: bool) -> ctypes.CDLL: ...


class _ContentMap(Protocol):
    def __call__(
        self, check: Callable[[_T], _U], inputs: Iterable[_T], *,
        on_result: Callable[[_U], object] | None = None,
    ) -> list[_U]: ...


class _SourceHashes(Protocol):
    def __call__(
        self, projects: Iterable[Path], mirror: _Mirror, *,
        native_path: Callable[[_Path], str], check_time: Callable[[], object],
        error_type: _Error, content_map: _ContentMap = ...,
        on_progress: Callable[[], object] | None = None,
    ) -> dict[Path, str]: ...


class _InspectSources(Protocol):
    def __call__(
        self, arguments: list[str], environment: dict[str, str], cwd: Path | None,
        trusted_source_roots: tuple[Path, ...], *, operation: str = "install",
    ) -> _SourceInputs: ...


class _Cutoff(Protocol):
    def __call__(
        self, settings: str, *, now: datetime | None = None, _operation: str = "install",
    ) -> list[str]: ...


class _TransactionFactory(Protocol[_T_co]):
    def __call__(
        self, argv: tuple[str, ...], environment: dict[str, str], cwd: Path,
        sources: dict[Path, Path], source_hashes: dict[Path, str],
    ) -> _T_co: ...


class _RetainOptions(TypedDict, total=False):
    env: dict[str, str] | None
    cwd: Path | None
    now: datetime | None
    trusted_source_roots: tuple[Path, ...]
    retained_resource_roots: tuple[Path, ...]
    non_editable: bool
    _operation: str
    deadline: float | None
    native_target: bool
    _projection: list[str] | None
    on_progress: Callable[[], object] | None


class _Retain(Protocol[_T_co]):
    def __call__(
        self, uv: str, arguments: list[str], *, env: dict[str, str] | None = None,
        cwd: Path | None = None, now: datetime | None = None,
        trusted_source_roots: tuple[Path, ...] = (),
        retained_resource_roots: tuple[Path, ...] = (), non_editable: bool = False,
        _operation: str = "install", _generated_constraints: tuple[Path, ...] = (),
        deadline: float | None = None, native_target: bool = False,
        _projection: list[str] | None = None, on_progress: Callable[[], object] | None = None,
    ) -> ContextManager[_T_co]: ...


class _Finalizer(Protocol):
    def detach(self) -> object: ...


class _TemporaryInputs(Protocol):
    name: str
    _finalizer: _Finalizer

    def _rmtree(self, name: str, *, ignore_errors: bool) -> None: ...


class _Remap(Protocol):
    def __call__(self, value: _T) -> _T: ...


class _ProjectMetadata(TypedDict, total=False):
    name: str
    dependencies: list[object]


class _ProjectDocument(TypedDict):
    project: _ProjectMetadata
    tool: dict[str, dict[str, dict[str, object]]]

_arguments_path = Path(__file__).with_name("installer_dependency_arguments.py")
_arguments = types.ModuleType(__name__ + "_arguments")
_arguments.__file__ = str(_arguments_path)
exec(compile(_arguments_path.read_bytes(), str(_arguments_path), "exec"), _arguments.__dict__)
argument_pairs = cast(_ArgumentPairs, _arguments.argument_pairs)
balanced = cast(_RawBalanced, _arguments.balanced)
uv_command = cast(_UVCommand, _arguments.uv_command)
if not TYPE_CHECKING:
    captured_environment = _arguments.captured_environment
    check_compile_projection = _arguments.check_compile_projection
    cutoff_arguments = _arguments.cutoff_arguments
    cutoff_value = _arguments.cutoff_value
    merge_config = _arguments.merge_config
    package_cutoffs = _arguments.package_cutoffs
    resolver_configuration = _arguments.resolver_configuration

_WINDOWS = os.name == "nt"
_TRUE_VALUES = {"y", "yes", "t", "true", "on", "1"}
_FALSE_VALUES = {"n", "no", "f", "false", "off", "0"}


def require_system_config_provider(
    uv: str, environment: Mapping[str, str], *, deadline: float | None = None,
    error_type: _Error, cwd: Path | None = None,
) -> str:
    """Do not reinterpret an option that the selected UV would silently ignore."""
    flag = environment.get("UV_NO_SYSTEM_CONFIG")
    if flag is None:
        return uv
    if flag.lower() not in _TRUE_VALUES | _FALSE_VALUES:
        raise error_type("UV_NO_SYSTEM_CONFIG must contain a supported boolean value")
    if flag.lower() in _FALSE_VALUES:
        return uv
    if not uv:
        raise error_type("UV_NO_SYSTEM_CONFIG requires its selected UV provider")
    if not _WINDOWS:
        uv = _arguments.uv_launch_path(uv, environment, cwd, error_type=error_type)
    elif not Path(uv).is_absolute():
        uv = select_uv_executable(os.path.abspath(uv), error_type=error_type)
    try:
        result = subprocess.run(
            [uv, "--version"], env=dict(environment), cwd=cwd, capture_output=True, text=True,
            timeout=remaining_timeout(deadline, error_type=error_type, cap=30),
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0) if _WINDOWS else 0,
        )
    except (OSError, UnicodeError, subprocess.TimeoutExpired) as exc:
        raise error_type("Cannot qualify UV_NO_SYSTEM_CONFIG support") from exc
    version = re.fullmatch(r"uv ([0-9]+)\.([0-9]+)\.([0-9]+)(?: \([^\r\n]*\))?\s*", result.stdout)
    if result.returncode or version is None or tuple(map(int, version.group(1, 2, 3))) < (0, 11, 16):
        raise error_type("UV_NO_SYSTEM_CONFIG requires UV 0.11.16 or newer")
    return uv


def immutable_install_arguments(
    arguments: Iterable[str], pairs: Sequence[tuple[str, str]], *, error_type: _Error,
) -> list[str]:
    if any(option in ("-e", "--editable") for option, _ in pairs):
        raise error_type("deployed runtimes require non-editable immutable versioned slots")
    if any(option == "--link-mode" and value != "copy" for option, value in pairs):
        raise error_type("deployed runtimes require copied installation artifacts")
    result = list(arguments)
    if not any(option == "--link-mode" for option, _ in pairs):
        result.extend(["--link-mode", "copy"])
    return result


_content_path = Path(__file__).with_name("installer_retained_content.py")
_content = types.ModuleType(__name__ + "_content")
_content.__file__ = str(_content_path)
exec(compile(_content_path.read_bytes(), str(_content_path), "exec"), _content.__dict__)
bounded_content_map = cast(_ContentMap, _content.bounded_content_map)
retained_source_hashes = cast(_SourceHashes, _content.retained_source_hashes)
verify_retained_sources = retained_source_hashes




@overload
def remaining_timeout(deadline: float | None, *, error_type: _Error, cap: float) -> float: ...


@overload
def remaining_timeout(
    deadline: float | None, *, error_type: _Error, cap: None = None,
) -> float | None: ...


def remaining_timeout(
    deadline: float | None, *, error_type: _Error, cap: float | None = None,
) -> float | None:
    if deadline is None:
        return cap
    if isinstance(deadline, bool) or not isinstance(deadline, (int, float)) or not math.isfinite(deadline):
        raise error_type("retained UV inputs require a finite monotonic deadline")
    remaining = deadline - clock.monotonic()
    if remaining <= 0:
        raise error_type("retained UV input deadline exceeded")
    return remaining if cap is None else min(remaining, cap)


def copy_retained_file(
    source: _Path, destination: _PathT, *, native_path: Callable[[_Path], str],
    deadline: float | None, error_type: _Error, on_progress: Callable[[], object] | None = None,
) -> str | _PathT:
    remaining_timeout(deadline, error_type=error_type)
    if deadline is None:
        result: str | _PathT = shutil.copy2(native_path(source), native_path(destination))
    else:
        with open(native_path(source), "rb") as incoming, open(native_path(destination), "wb") as output:
            while True:
                remaining_timeout(deadline, error_type=error_type)
                chunk = incoming.read(1 << 20)
                if not chunk:
                    break
                output.write(chunk)
        shutil.copystat(native_path(source), native_path(destination))
        result = destination
    remaining_timeout(deadline, error_type=error_type)
    if on_progress is not None:
        on_progress()
    return result


def prepare_uv_install(
    uv: str, arguments: list[str], *, environment: dict[str, str], cwd: Path | None,
    now: datetime | None, trusted_source_roots: tuple[Path, ...], projection: list[str] | None,
    operation: str, deadline: float | None, native_target: bool,
    compile_inputs: Callable[[list[str], dict[str, str], Path | None, tuple[Path, ...], str], list[str]],
    source_inputs: _InspectSources, select_uv: Callable[[str], str],
    cutoff: _Cutoff, argument_pairs: _Pairs, balanced: _Balanced, error_type: _Error,
    settings_timeout: float, headless: int, value_options: Set[str],
) -> list[str]:
    remaining_timeout(deadline, error_type=error_type)
    uv = select_uv(uv)
    uv = require_system_config_provider(uv, environment, cwd=cwd, deadline=deadline, error_type=error_type)
    if native_target and environment.get("UV_PYTHON_PLATFORM"):
        raise error_type("native source lock export cannot override its selected target")
    if operation in ("compile", "source-compile"):
        arguments = compile_inputs(arguments, environment, cwd, trusted_source_roots, operation)
    base, *_ = source_inputs(
        arguments, environment, cwd, trusted_source_roots,
        **({"operation": operation} if operation != "install" else {}),
    )
    remaining_timeout(deadline, error_type=error_type)
    probe_arguments = [arg for arg in arguments if arg != "--quiet" and not re.fullmatch(r"-q+", arg)]
    native_operation = "compile" if operation == "source-compile" else operation
    command, probe_cwd = uv_command(
        uv, ["--show-settings", *probe_arguments], cwd=cwd, directory=base,
        operation=native_operation, windows=os.name == "nt", argument_pairs=argument_pairs,
        value_options=value_options,
    )
    try:
        result = subprocess.run(
            command, capture_output=True, text=True, env=environment, cwd=probe_cwd,
            timeout=remaining_timeout(deadline, error_type=error_type, cap=settings_timeout),
            creationflags=headless,
        )
    except (OSError, subprocess.TimeoutExpired) as exc:
        raise error_type("UV seasoning configuration probe failed or timed out") from exc
    remaining_timeout(deadline, error_type=error_type)
    if result.returncode:
        raise error_type("UV seasoning configuration probe failed")
    admitted = cutoff(result.stdout, now=now, _operation=native_operation)
    if native_operation == "compile":
        check_compile_projection(
            result.stdout, arguments, argument_pairs=argument_pairs,
            balanced=balanced, error_type=error_type,
            source_graph=operation == "source-compile", native_target=native_target,
        )
    elif native_target:
        raise error_type("native target binding requires a compile transaction")
    if projection is not None:
        projection.append(result.stdout)
    cleaned = []
    iterator = iter(arguments)
    for argument in iterator:
        if argument in ("--exclude-newer", "--exclude-newer-package"):
            next(iterator, None)
        elif (not argument.startswith(("--exclude-newer=", "--exclude-newer-package="))
              and argument not in ("--reinstall", "--force-reinstall")):
            cleaned.append(argument)
    return [*cleaned, *admitted]


# native-executable:begin
def physical_executable(path: str) -> str | None:
    """Read link targets before inspecting descendants, including the final file.

    RedirectionGuard can refuse traversal of a junction even when its target is
    usable. Like versioned-runtime's legacy link handling, inspect the entry
    with lstat and readlink instead of resolving it with a traversing stat.
    """
    requested = Path(path)
    if not requested.is_absolute():
        requested = Path.cwd() / requested
    remaining = list(requested.parts)
    current = remaining.pop(0)
    links = 0
    if not remaining:
        return None
    while remaining:
        component = remaining.pop(0)
        if component == "..":
            current = os.path.dirname(current)
            if not remaining:
                return None
            continue
        candidate = os.path.join(current, component)
        try:
            info = os.lstat(candidate)
        except (FileNotFoundError, NotADirectoryError):
            return None
        if (
            stat.S_ISLNK(info.st_mode)
            or getattr(info, "st_reparse_tag", 0) & 0x20000000
        ):
            links += 1
            if links > 40:
                raise OSError(errno.ELOOP, "Interpreter link cycle or excessive indirection", path)
            target = os.readlink(candidate)
            if not os.path.isabs(target):
                target = os.path.join(current, target)
            remaining = list(Path(target).parts) + remaining
            current = remaining.pop(0)
            if not remaining:
                return None
            continue
        if remaining and not stat.S_ISDIR(info.st_mode):
            return None
        if not remaining and not stat.S_ISREG(info.st_mode):
            return None
        current = candidate
    return current
# native-executable:end


def select_uv_executable(uv: str, *, error_type: _Error) -> str:
    if not _WINDOWS:
        return uv
    if str(uv).lower() in ("uv", "uv.exe"):
        import ctypes

        kernel = cast(_WindowsLibrary, getattr(ctypes, "WinDLL"))("kernel32", use_last_error=True)
        kernel.SearchPathW.argtypes = [
            ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_wchar_p,
            ctypes.c_uint32, ctypes.c_wchar_p, ctypes.c_void_p,
        ]
        kernel.SearchPathW.restype = ctypes.c_uint32
        buffer = ctypes.create_unicode_buffer(32768)
        length = kernel.SearchPathW(None, str(uv), ".exe", len(buffer), buffer, None)
        if not length:
            get_last_error = cast(Callable[[], int], getattr(ctypes, "get_last_error"))
            raise error_type(f"Native Windows UV lookup failed (error {get_last_error()})")
        if length >= len(buffer):
            raise error_type("Selected UV executable exceeds the native path limit")
        uv = buffer.value
    if not Path(uv).is_absolute() or Path(uv).suffix.lower() != ".exe":
        return uv
    selected = physical_executable(uv)
    if selected is None:
        raise error_type("Selected UV executable does not identify an available regular file")
    return selected


class RetainedInputCleanupError(RuntimeError):
    """Installation inputs could not be removed after execution settled."""


def report_cleanup_error(error: BaseException) -> None:
    print(f"retained-input-cleanup: {error}", file=sys.stderr)


def native_retained_path(path: _Path) -> str:
    value = os.path.abspath(path)
    if os.name != "nt" or value.startswith("\\\\?\\"):
        return value
    return "\\\\?\\UNC\\" + value[2:] if value.startswith("\\\\") else "\\\\?\\" + value


def file_uri(path: Path | PureWindowsPath) -> str:
    """Keep extended Windows filesystem spelling out of file URI authorities."""
    if isinstance(path, PureWindowsPath) and str(path).startswith("\\\\?\\"):
        value = str(path)
        path = PureWindowsPath("\\\\" + value[8:] if value[:8].lower() == "\\\\?\\unc\\" else value[4:])
    return path.as_uri()


@contextmanager
def retain_closed_wheels(
    uv: str, snapshot: object, *, python: _Path, env: dict[str, str] | None,
    captured_environment: Callable[[dict[str, str] | None], dict[str, str]],
    select_uv: Callable[[str], str], transaction_type: _TransactionFactory[_T], error_type: _Error,
) -> Iterator[_T]:
    """Retain the policy owner's fixed, closed wheel invocation projection."""
    from installer_wheel_snapshot import WheelSnapshot

    if not isinstance(snapshot, WheelSnapshot):
        raise error_type("closed wheel installation requires retained wheel custody")
    python_path = Path(python)
    if not python_path.is_absolute():
        raise error_type("closed wheel interpreter must be an absolute candidate path")
    uv = select_uv(uv)
    if not Path(uv).is_absolute():
        selected: str | None
        if os.path.dirname(uv):
            selected = uv
        else:
            import shutil

            selected = shutil.which(uv)
            if selected is None:
                raise error_type("closed wheel UV executable is unavailable")
        uv = select_uv(os.path.abspath(selected))
    environment = {
        key: value for key, value in captured_environment(env).items()
        if (key in ("UV_CONCURRENT_INSTALLS", "UV_CONCURRENT_DOWNLOADS", "UV_CONCURRENT_BUILDS")
            or not key.upper().startswith(("UV_", "PIP_", "PYTHON")))
        and key.upper() not in ("VIRTUAL_ENV", "CONDA_PREFIX", "__PYVENV_LAUNCHER__")
    }
    with temporary_inputs(None) as temporary:
        root = private_retained_root(Path(temporary), error_type=error_type).resolve()
        requirements = root / "wheels.txt"
        requirements.write_text("".join(
            f"{name.split('-', 1)[0]} @ "
            f"{file_uri(path)}#sha256={snapshot.digests[name].removeprefix('sha256:')} "
            f"--hash={snapshot.digests[name]}\n"
            for name, path in sorted(snapshot.paths.items())
        ), encoding="utf-8")
        snapshot.verify()
        yield transaction_type(
            (uv, "pip", "install", "--no-config", "--no-cache", "--offline",
             "--no-python-downloads", "--no-index", "--no-deps", "--no-build",
             "--no-sources", "--require-hashes", "--link-mode", "copy",
             "--python", native_retained_path(python_path), "--requirements", str(requirements)),
            environment, root, {}, {},
        )
        snapshot.verify()


@contextmanager
def temporary_inputs(parent: _Path | None) -> Iterator[str]:
    prefix = "uv-" if parent is not None else "seasoned-uv-"
    temporary = cast(_TemporaryInputs, tempfile.TemporaryDirectory(prefix=prefix, dir=parent))
    # Retaining owners, not collection or interpreter shutdown, retire inputs.
    temporary._finalizer.detach()
    failed = False
    abandoned = False
    try:
        yield temporary.name
    except GeneratorExit:
        abandoned = True
        raise
    except BaseException:
        failed = True
        raise
    finally:
        if not abandoned:
            temporary.name = native_retained_path(temporary.name)
            try:
                # cleanup() can hide access errors behind exists() after detachment.
                temporary._rmtree(temporary.name, ignore_errors=False)
            except OSError as exc:
                if failed:
                    report_cleanup_error(exc)
                else:
                    raise RetainedInputCleanupError(str(exc)) from exc


@contextmanager
def retain_local_exclusions(
    retain: _Retain[_T], uv: str, arguments: list[str], *,
    excluded_local_packages: tuple[str, ...], error_type: _Error, **options: object,
) -> Iterator[_T]:
    retained_options = cast(_RetainOptions, options)
    if not isinstance(excluded_local_packages, tuple) or any(
        not isinstance(name, str)
        or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is None
        for name in excluded_local_packages
    ):
        raise error_type("excluded local packages must be an immutable tuple of names")
    names = tuple(re.sub(r"[-_.]+", "-", name).lower() for name in excluded_local_packages)
    if len(set(names)) != len(names):
        raise error_type("excluded local packages contain duplicate names")
    environment = retained_options.get("env")
    environment = captured_environment(
        os.environ if environment is None else environment, windows=_WINDOWS, error_type=error_type,
    )
    retained_options["env"] = environment
    uv = select_uv_executable(uv, error_type=error_type)
    uv = require_system_config_provider(
        uv, environment, cwd=retained_options.get("cwd"),
        deadline=retained_options.get("deadline"), error_type=error_type,
    )
    if not names:
        with retain(uv, arguments, **retained_options) as transaction:
            yield transaction
        return
    with temporary_inputs(None) as temporary:
        constraint = (Path(temporary) / "excluded-local-packages.txt").resolve()
        constraint.write_text(
            "".join(f"{name}<0,>=0\n" for name in sorted(names)), encoding="utf-8",
        )
        option = "--constraints" if options.get("_operation") in ("compile", "source-compile") else "--constraint"
        with retain(
            uv, [*arguments, option, str(constraint)],
            _generated_constraints=(constraint,), **retained_options,
        ) as transaction:
            yield transaction


def retained_source_layout(
    root: Path, projects: set[Path], files: set[Path], resources: Iterable[Path],
    base: Path, arguments: list[str], environment: dict[str, str], editables: set[Path], *,
    argument_pairs: _Pairs, file_options: set[str], toml: _Toml,
    config_inputs: Callable[[list[str], dict[str, str], Path], list[tuple[Path, bool]]],
    generated_constraints: tuple[Path, ...] = (),
) -> tuple[dict[str, Path], _Mirror]:
    context_project = dict(argument_pairs(arguments)).get("--project") or environment.get("UV_PROJECT")
    pairs = list(argument_pairs(arguments))
    operands = [value for option, value in pairs if not option]
    layout_files = files - set(generated_constraints)
    compact = (
        projects and operands and not layout_files and not editables and not context_project
        and not any(
            option in file_options | {"--group"}
            and (option == "--group" or (base / value).resolve() not in generated_constraints)
            for option, value in pairs
        )
        and not any(environment.get(key) for key in (
            "UV_CONSTRAINT", "UV_BUILD_CONSTRAINT", "UV_OVERRIDE", "UV_EXCLUDE",
        ))
        and all(Path(value).is_absolute() for value in operands)
        and not any(metadata_needs_caller_layout(toml(project / "pyproject.toml")) for project in projects)
        and not any(metadata_needs_caller_layout(toml(path))
                    for path, _ in config_inputs(arguments, environment, base))
    )
    # Generated absolute constraints must not widen the caller-relative source layout.
    paths = [*projects, *layout_files, *resources]
    if not compact:
        paths.append(base)
    if context_project:
        paths.append((base / context_project).resolve())
    anchors = {}
    for path in paths:
        anchor = path.anchor.lower()
        members = [str(member) for member in paths if member.anchor.lower() == anchor]
        common = Path(os.path.commonpath(members))
        anchors[anchor] = common.parent if common.is_file() else common

    def mirror(path: Path) -> Path:
        path = Path(path).resolve()
        if path in generated_constraints:
            return root / "generated" / path.name
        anchor = hashlib.sha256(path.anchor.encode()).hexdigest()[:12]
        return root / anchor / path.relative_to(anchors[path.anchor.lower()])

    return anchors, mirror


def retained_source_remapper(paths: Iterable[Path], mirror: _Mirror) -> _Remap:
    replacements = {}
    for path in sorted(paths, key=lambda item: len(str(item)), reverse=True):
        retained_path = mirror(path)
        for before, after in (
            (path.as_uri(), retained_path.as_uri()),
            (str(path), str(retained_path)),
            (path.as_posix(), retained_path.as_posix()),
            (json.dumps(str(path))[1:-1], json.dumps(str(retained_path))[1:-1]),
        ):
            replacements[before] = after
    pattern = re.compile("|".join(re.escape(before) for before in replacements)) if replacements else None

    def remap(value: _T) -> _T:
        if isinstance(value, list):
            return cast(_T, [remap(item) for item in cast(list[object], value)])
        if isinstance(value, dict):
            return cast(_T, {key: remap(item) for key, item in cast(dict[object, object], value).items()})
        if not isinstance(value, str) or pattern is None:
            return value
        return cast(_T, pattern.sub(lambda match: replacements[match[0]], value))

    return remap


def config_inputs(
    arguments: list[str], environment: dict[str, str], base: Path, *,
    argument_pairs: _Pairs, toml: _Toml, error_type: _Error,
) -> list[tuple[Path, bool]]:
    pairs = dict(argument_pairs(arguments))
    if "--no-config" in pairs or environment.get("UV_NO_CONFIG", "").lower() in ("1", "true"):
        return []
    explicit = pairs.get("--config-file") or environment.get("UV_CONFIG_FILE")
    if explicit:
        return [(base / explicit, False)]
    system_flag = environment.get("UV_NO_SYSTEM_CONFIG")
    if system_flag is not None and system_flag.lower() not in _TRUE_VALUES | _FALSE_VALUES:
        raise error_type("UV_NO_SYSTEM_CONFIG must contain a supported boolean value")
    no_system = system_flag is not None and system_flag.lower() in _TRUE_VALUES
    candidates = []
    project = base / (pairs.get("--project") or environment.get("UV_PROJECT") or ".")
    if "--isolated" not in pairs and environment.get("UV_ISOLATED", "").lower() not in ("1", "true"):
        for directory in (project, *project.parents):
            uv = directory / "uv.toml"
            manifest = directory / "pyproject.toml"
            if uv.is_file():
                candidates.append((uv, False))
                break
            if manifest.is_file():
                tool = toml(manifest).get("tool", {})
                if not isinstance(tool, dict):
                    raise error_type("configured project metadata is unsupported")
                if "uv" in tool:
                    if not isinstance(tool["uv"], dict):
                        raise error_type("configured UV metadata is unsupported")
                    candidates.append((manifest, True))
                    break
    if os.name == "nt":
        if environment.get("APPDATA"):
            candidates.append((Path(environment["APPDATA"]) / "uv" / "uv.toml", False))
        if not no_system and environment.get("SYSTEMDRIVE"):
            candidates.append((
                Path(environment["SYSTEMDRIVE"] + "\\") / "ProgramData" / "uv" / "uv.toml", False,
            ))
    else:
        user = Path(environment.get("XDG_CONFIG_HOME") or (
            Path(environment.get("HOME") or Path.home()) / ".config"
        )) / "uv" / "uv.toml"
        candidates.append((user, False))
        if not no_system:
            system = Path("/etc/uv/uv.toml")
            for config_directory in (environment.get("XDG_CONFIG_DIRS") or "/etc/xdg").split(":"):
                if not config_directory:
                    continue
                candidate = base / config_directory / "uv" / "uv.toml"
                if candidate.is_file():
                    system = candidate
                    break
            candidates.append((system, False))
    return [(path, is_project) for path, is_project in candidates if path.is_file()]


def freeze_source_editables(data: dict[str, object]) -> None:
    document = cast(_ProjectDocument, data)
    sources = document.get("tool", {}).get("uv", {}).get("sources", {})
    for entries in sources.values():
        for source in cast(list[dict[str, object]], entries if isinstance(entries, list) else [entries]):
            if source.get("editable") is True:
                source["editable"] = False


class _CaseSensitiveConfigParser(configparser.ConfigParser):
    def optionxform(self, optionstr: str) -> str:
        return str(optionstr)


def retain_editable_metadata(project: Path, *, error_type: _Error) -> None:
    configuration = Path(native_retained_path(project / "setup.cfg"))
    config = _CaseSensitiveConfigParser(interpolation=None)
    try:
        if configuration.exists():
            config.read_string(configuration.read_text(encoding="utf-8"))
        if not config.has_section("egg_info"):
            config.add_section("egg_info")
        config.set("egg_info", "egg_base", ".")
        with configuration.open("w", encoding="utf-8") as stream:
            config.write(stream)
    except (OSError, configparser.Error) as error:
        raise error_type("cannot retain editable build metadata") from error


def metadata_needs_caller_layout(value: object) -> bool:
    if isinstance(value, dict):
        # Source-bearing metadata can contain relative references or traversals
        # outside and back into a source root. Keep its established layout.
        if any(value.get(key) for key in (
            "sources", "dependency-groups", "dev-dependencies", "constraint-dependencies",
            "override-dependencies", "build-constraint-dependencies",
            "dependency-metadata", "extra-build-dependencies",
        )):
            return True
        return any(metadata_needs_caller_layout(item) for item in cast(dict[object, object], value).values())
    if isinstance(value, list):
        return any(metadata_needs_caller_layout(item) for item in cast(list[object], value))
    return isinstance(value, str) and (
        "file:" in value.lower() or re.search(r"(?:^|[\s/\\])\.\.(?:[/\\]|$)", value) is not None
    )


def source_compile_packages(
    projects: Iterable[Path], *, toml: _Toml, identity: Callable[[str], str], error_type: _Error,
) -> tuple[str, ...]:
    manifests = {}
    for path in projects:
        data = cast(_ProjectDocument, toml(path / "pyproject.toml"))
        manifests[path] = data
    names = [identity(data["project"]["name"]) for data in manifests.values()]
    if not names or len(set(names)) != len(names):
        raise error_type("source lock export needs an unambiguous owned project graph")
    root_values: object = getattr(projects, "roots", ())
    if not isinstance(root_values, (set, frozenset, list, tuple)):
        raise error_type("source lock export lacks its actual project roots")
    pending: list[Path] = []
    for root in root_values:
        if not isinstance(root, Path):
            raise error_type("source lock export lacks its actual project roots")
        pending.append(root)
    if not pending or not set(pending) <= manifests.keys():
        raise error_type("source lock export lacks its actual project roots")
    active: set[Path] = set()
    while pending:
        path = pending.pop()
        if path in active:
            continue
        active.add(path)
        data = manifests[path]
        project = data["project"]
        required = {
            identity(match[1])
            for requirement in project.get("dependencies", [])
            if isinstance(requirement, str) and ";" not in requirement and "@" not in requirement
            and (match := re.match(r"\s*([A-Za-z0-9][A-Za-z0-9._-]*)", requirement))
        }
        for name, mapping in data.get("tool", {}).get("uv", {}).get("sources", {}).items():
            if isinstance(mapping, dict) and "path" in mapping:
                if identity(name) in required:
                    relative = mapping["path"]
                    if not isinstance(relative, str):
                        raise error_type("source lock export has an invalid local dependency path")
                    child = (path / relative).resolve()
                    if child not in manifests:
                        raise error_type("source lock export lacks an owned local dependency")
                    pending.append(child)
    selected = {identity(manifests[path]["project"]["name"]) for path in active}
    omissions = {
        identity(name)
        for data in manifests.values()
        for name, mapping in data.get("tool", {}).get("uv", {}).get("sources", {}).items()
        if isinstance(mapping, dict) and "path" in mapping
    }
    if not omissions <= selected:
        raise error_type("source lock export cannot omit a conditional or unselected local dependency")
    return tuple(sorted(omissions))


def source_compile_arguments(
    arguments: list[str], environment: dict[str, str], cwd: Path | None,
    roots: tuple[Path, ...], operation: str, *, source_inputs: _InspectSources,
    toml: _Toml, identity: Callable[[str], str], argument_pairs: _Pairs, error_type: _Error,
) -> list[str]:
    packages: tuple[str, ...] = ()
    if operation == "source-compile":
        _, projects, _, _ = source_inputs(
            arguments, environment, cwd, roots, operation=operation,
        )
        packages = source_compile_packages(projects, toml=toml, identity=identity, error_type=error_type)
    return compile_arguments(
        arguments, argument_pairs=argument_pairs, error_type=error_type, source_packages=packages,
        source_graph=operation == "source-compile",
    )


def compile_arguments(
    arguments: list[str], *, argument_pairs: _Pairs, error_type: _Error,
    source_packages: tuple[str, ...] = (), source_graph: bool = False,
) -> list[str]:
    """Bounded requirements export, never an implicit incremental lock update."""
    values = {
        "--constraints", "-c", "--overrides", "--excludes", "--build-constraints", "-b",
        "--extra", "--cert", "-o", "--output-file", "--format", "--only-binary",
        "--python-platform", "--python-version", "--index", "--default-index",
        "--index-url", "-i", "--extra-index-url", "--index-strategy", "--keyring-provider",
        "--resolution", "--prerelease", "--fork-strategy", "--exclude-newer",
        "--exclude-newer-package", "--cache-dir", "-p", "--python", "--color",
        "--directory", "--project", "--config-file",
    }
    flags = {
        "--all-extras", "--generate-hashes", "--no-build", "--no-index", "--universal",
        "--upgrade", "-U", "--no-sources", "--no-cache", "-n", "--refresh",
        "--no-python-downloads", "--no-managed-python", "--offline", "--system-certs",
        "--no-progress", "--quiet", "-q", "--verbose", "-v", "--no-header",
        "--no-annotate",
    }
    result = []
    omissions = []
    output_format = "requirements.txt"
    for option, value in argument_pairs(arguments):
        if not option:
            if value == "-" or not value:
                raise error_type("lock export requires retained file inputs, not standard input")
            result.append(value)
        elif option == "--no-emit-package":
            omissions.append(value)
        elif option == "--format":
            if value not in ("requirements.txt", "pylock.toml"):
                raise error_type("lock export format is unsupported for this transaction")
            output_format = value
        elif option in ("--generate-hashes", "--upgrade", "-U", "--no-header", "--no-annotate") and value:
            raise error_type("lock export boolean options cannot carry values")
        elif option in ("--format", "--generate-hashes", "--upgrade", "-U", "--no-header", "--no-annotate"):
            continue
        elif option in values:
            if option in ("-o", "--output-file") and value == "-":
                raise error_type("lock export output must be a file path or implicit standard output")
            result.extend([option, value])
        elif option in flags and not value:
            result.append(option)
        else:
            raise error_type("unsupported lock export option")
    if omissions and tuple(omissions) != source_packages:
        raise error_type("lock export omissions must match the retained source graph")
    return [*result, "--format", output_format, "--generate-hashes", "--upgrade", "--no-header", "--no-annotate",
            *[argument for name in source_packages for argument in ("--no-emit-package", name)]]


def check_requirements_sources(
    path: Path, seen: set[Path], roots: tuple[Path, ...], base: Path,
    projects: set[Path] | None, files: set[Path] | None, editables: set[Path] | None, *,
    check_project: Callable[[Path, tuple[Path, ...], set[Path]], None],
    check_requirement: Callable[[str, Path, tuple[Path, ...], set[Path]], Path | None],
    error_type: _Error,
) -> None:
    projects = set() if projects is None else projects
    if path.suffix == ".toml":
        if path.name != "pyproject.toml":
            raise error_type("dependency TOML input lacks attributable project identity")
        check_project(path.parent, roots, projects)
        return
    path = path.resolve()
    if path in seen:
        raise error_type("cyclic requirement includes cannot prove dependency sources")
    seen = seen | {path}
    if files is not None:
        files.add(path)
    try:
        text = re.sub(r"\\\r?\n", " ", path.read_text(encoding="utf-8"))
    except OSError as exc:
        raise error_type("dependency sources file could not be read") from exc
    for line in text.splitlines():
        line = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        if not line or line.startswith("#"):
            continue
        include = re.fullmatch(r"(?:-r|--requirements?|--constraints?|-c)\s+(.+)", line)
        if include:
            check_requirements_sources(
                path.parent / include[1], seen, roots, base, projects, files, editables,
                check_project=check_project, check_requirement=check_requirement, error_type=error_type,
            )
        elif line.startswith(("--index-url ", "--extra-index-url ", "--index-url=", "--extra-index-url=")):
            continue
        elif line.startswith(("-e ", "--editable ")):
            source = check_requirement(line.split(maxsplit=1)[1], base, roots, projects)
            if source is not None and editables is not None:
                editables.add(source)
        elif line.startswith("-"):
            raise error_type("unsupported dependency sources option")
        else:
            check_requirement(re.sub(r"\s+--hash=\S+", "", line), base, roots, projects)


def compile_requirement_includes(
    text: str, source: Path, mirror: _Mirror, remap: Callable[[str], str] | None = None,
) -> str:
    """Retain include targets by identity, not by lexical path replacement."""
    lines = []
    for line in re.sub(r"\\\r?\n", " ", text).splitlines():
        requirement = re.split(r"\s+#", line, maxsplit=1)[0].strip()
        include = re.fullmatch(r"(-r|--requirements?|--constraints?|-c)\s+(.+)", requirement)
        if include:
            line = include[1] + " " + mirror(source.parent / include[2]).as_posix()
        elif remap is not None:
            line = remap(line)
        lines.append(line)
    return "\n".join(lines) + "\n"


def _toml_value(value: object, error_type: _Error) -> str:
    if isinstance(value, str):
        return json.dumps(value, ensure_ascii=True)
    if isinstance(value, bool):
        return str(value).lower()
    if isinstance(value, (int, float)):
        return repr(value)
    if isinstance(value, (datetime, date, time)):
        return value.isoformat()
    if isinstance(value, list):
        return "[" + ", ".join(_toml_value(item, error_type) for item in cast(list[object], value)) + "]"
    if isinstance(value, dict):
        return "{" + ", ".join(
            json.dumps(key) + " = " + _toml_value(item, error_type)
            for key, item in cast(dict[str, object], value).items()
        ) + "}"
    raise error_type("dependency input TOML value cannot be retained")


def write_toml(path: Path, data: Mapping[str, object], *, error_type: _Error) -> None:
    text = "\n".join(
        json.dumps(key) + " = " + _toml_value(value, error_type) for key, value in data.items()
    )
    # The private snapshot can contain credential-bearing index settings.
    if path.exists():
        os.chmod(path, 0o600)
    with path.open("w", encoding="utf-8") as stream:
        os.chmod(path, 0o600)
        stream.write(text + "\n")


def private_retained_root(parent: Path, *, error_type: _Error, name: str = "inputs") -> Path:
    if not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]{0,127}", name) is None:
        raise error_type("private dependency-input directory requires one ordinary name")
    root = parent / name
    if os.name != "nt":
        root.mkdir(mode=0o700)
        return root
    import ctypes
    from ctypes import wintypes

    class SecurityAttributes(ctypes.Structure):
        _fields_ = [("length", wintypes.DWORD), ("descriptor", wintypes.LPVOID), ("inherit", wintypes.BOOL)]

    advapi = cast(_WindowsLibrary, getattr(ctypes, "WinDLL"))("advapi32", use_last_error=True)
    kernel = cast(_WindowsLibrary, getattr(ctypes, "WinDLL"))("kernel32", use_last_error=True)
    convert = advapi.ConvertStringSecurityDescriptorToSecurityDescriptorW
    convert.argtypes = [wintypes.LPCWSTR, wintypes.DWORD, ctypes.POINTER(wintypes.LPVOID), wintypes.LPVOID]
    convert.restype = wintypes.BOOL
    create = kernel.CreateDirectoryW
    create.argtypes = [wintypes.LPCWSTR, ctypes.POINTER(SecurityAttributes)]
    create.restype = wintypes.BOOL
    kernel.LocalFree.argtypes = [wintypes.LPVOID]
    kernel.LocalFree.restype = wintypes.LPVOID
    descriptor = wintypes.LPVOID()
    # Protected, owner-only DACL propagates to both files and directories.
    if not convert("D:P(A;OICI;FA;;;OW)", 1, ctypes.byref(descriptor), None):
        raise error_type("private dependency-input permissions could not be constructed")
    try:
        attributes = SecurityAttributes(ctypes.sizeof(SecurityAttributes), descriptor, False)
        if not create(native_retained_path(root), ctypes.byref(attributes)):
            raise error_type("private dependency-input directory could not be created")
    finally:
        kernel.LocalFree(descriptor)
    return root
