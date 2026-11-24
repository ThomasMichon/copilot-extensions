"""Python-owned runtime construction, shared by independently shipped installers."""

from __future__ import annotations
from collections.abc import Callable, Iterator

import codecs
import hashlib
import io
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import tempfile
import time
from contextlib import AbstractContextManager, ExitStack, contextmanager
from dataclasses import dataclass, replace
from functools import partial
from pathlib import Path
from typing import Any, TypedDict, cast

if sys.version_info >= (3, 11):
    import tomllib
else:
    from installer_dependency_policy import _toml_parser
    tomllib = _toml_parser()
import versioned_runtime as versioned_runtime
import versioned_runtime_source as versioned_runtime_source
runtime, runtime_source = versioned_runtime, versioned_runtime_source
from installer_dependency_policy import (
    DependencyPolicyError as DependencyPolicyError, RetainedUVInstall, _file_uri, _native_retained_path as _native_retained_path,
    _bounded_content_map as _bounded_content_map,
    _private_retained_root as _private_retained_root, _select_uv_executable,
    _temporary_inputs as _temporary_inputs,
    retain_closed_wheel_install as retain_closed_wheel_install, retain_uv_install as retain_uv_install,
)
from installer_engine_process import (
    ChildLaunchError as ChildLaunchError,
    ChildTimeoutExpired as ChildTimeoutExpired,
    run_owned,
)
from installer_wheel_inputs import _check_deadline, _ordinary
from installer_provider_roots import check_construction
from installer_wheel_snapshot import (
    WheelInstallInputs, WheelReuseInputs as WheelReuseInputs, _digest as _snapshot_digest,
    _open as _snapshot_open, _unchanged as _snapshot_unchanged)

_open, _unchanged, _digest = _snapshot_open, _snapshot_unchanged, _snapshot_digest
from installer_source_build import (
    _package_roots as _package_roots,
    SourceWheelInputs, _selected_extras as _selected_extras,
    _source_deadline, _source_phase, retained_input_graph, retained_source_wheels,
    retained_inputs, frozen_procutil as _procutil, _package_name as _package_name,
    revalidate_input_content, check_dev_owner,
    _construction_hash as _construction_hash,
    _wheel_construction_hash as _wheel_construction_hash)
from installer_engine_health import (
    application_staging_environment as _application_staging_environment,
    application_uv_selection as application_uv_selection,
    HealthRepairRequired as HealthRepairRequired,
    _PREPARE_INSTALLED_CHECK,
    verify_installed_inputs as verify_installed_inputs,
    verify_application_wheels as verify_application_wheels,
)

_ProgressOptions = TypedDict("_ProgressOptions", {"on_progress": Callable[[], None]}, total=False)
_SourceFileOptions = TypedDict(
    "_SourceFileOptions", {"on_progress": Callable[[], None], "include_generated": bool}, total=False,
)
_DiagnosticOptions = TypedDict("_DiagnosticOptions", {"diagnostics_path": Path}, total=False)
_GENERATED_DIRECTORIES = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache", ".test-runs",
    "build", "dist", "node_modules",
}
@dataclass(frozen=True)
class FrozenInputs:
    plugin: Path
    packages: tuple[Path, ...]
    payload_hash: str
    transaction: RetainedUVInstall | None = None
    retained_hash: str | None = None
    extras: tuple[str, ...] = ()
    local_packages: tuple[str, ...] | None = None
    wheel_inputs: WheelInstallInputs | WheelReuseInputs | None = None
    source_wheels: SourceWheelInputs | None = None
    application: Path | None = None
    closed_wheel_source: bool = False
    diagnostics: bool = False


@dataclass(frozen=True)
class ReadyCandidate:
    slot: Path
    payload_hash: str
    reused: bool
    descriptor: int | None
    frozen_plugin: Path
    extras: tuple[str, ...] = ()
    local_packages: tuple[str, ...] | None = None


class ContentRepairRequired(RuntimeError):
    """Published bytes differ; a caller must select a distinct generation."""


def _selected_local_packages(
    plugin: Path, packages: tuple[Path, ...], selection: tuple[str, ...] | None,
) -> tuple[str, ...] | None:
    if selection is None:
        return None
    if not isinstance(selection, tuple) or any(
        not isinstance(name, str) or re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name) is None
        for name in selection
    ):
        raise ValueError("Local package selection must be an immutable tuple of distribution names")
    names = tuple(re.sub(r"[-_.]+", "-", name).lower() for name in selection)
    if len(set(names)) != len(names):
        raise ValueError("Local package selection contains duplicate distributions")
    unknown = set(names) - {_package_name(package) for package in packages}
    if unknown:
        raise ValueError(f"Local package selection is outside the source graph: {', '.join(sorted(unknown))}")
    return tuple(sorted({_package_name(plugin), *names}))


def _installed_packages(inputs: FrozenInputs) -> tuple[Path, ...]:
    return tuple(
        package for package in inputs.packages
        if inputs.local_packages is None or _package_name(package) in inputs.local_packages
    )


def _excluded_packages(inputs: FrozenInputs) -> list[str]:
    if inputs.local_packages is None:
        return []
    return sorted({_package_name(package) for package in inputs.packages} - set(inputs.local_packages))


def _source_files(
    roots: tuple[Path, ...], *, on_progress: Callable[[], None] | None = None,
    include_generated: bool = False,
) -> tuple[Path, ...]:
    files: set[Path] = set()

    def scan_error(error: OSError) -> None:
        raise error

    for root in roots:
        native_root = Path(_native_retained_path(root))
        for directory, children, names in os.walk(native_root, onerror=scan_error, followlinks=False):
            parent = root / Path(directory).relative_to(native_root)
            for name in list(children):
                path = parent / name
                mode = Path(_native_retained_path(path)).lstat().st_mode
                if not stat.S_ISDIR(mode):
                    raise ValueError(f"Runtime input directory is not ordinary: {path}")
                if not include_generated and (
                    name in _GENERATED_DIRECTORIES or name.endswith((".egg-info", ".dist-info"))
                ):
                    children.remove(name)
            for name in names:
                path = parent / name
                if not stat.S_ISREG(Path(_native_retained_path(path)).lstat().st_mode):
                    raise ValueError(f"Runtime input file is not ordinary: {path}")
                if include_generated or not name.endswith((".pyc", ".pyo")):
                    files.add(path)
            if on_progress is not None:
                on_progress()
    return tuple(sorted(files))


def _source_input_files(
    roots: tuple[Path, ...], *, closed_wheel_source: bool = False,
    on_progress: Callable[[], None] | None = None,
) -> tuple[Path, ...]:
    options: _SourceFileOptions = {}
    if on_progress is not None:
        options["on_progress"] = on_progress
    if closed_wheel_source:
        options["include_generated"] = True
    return _source_files(roots, **options)


def freeze_inputs(
    plugin: Path, destination: Path, *, deadline: float | None = None,
    on_progress: Callable[[], None] | None = None,
    additional_plugins: tuple[Path, ...] = (),
    closed_wheel_source: bool = False,
) -> FrozenInputs:
    """Copy and verify all local package inputs before admitting a numbered build.

    The caller owns a fresh destination outside the input trees. Relative package
    layout is preserved, so transitive ``tool.uv.sources`` paths remain attributable.
    Numbered construction consumes these copies non-editably, never a live checkout.
    A complete operation may retain additional applications in the same closure.
    """
    if deadline is not None:
        _check_deadline(deadline)
    if type(closed_wheel_source) is not bool:
        raise ValueError("Closed-wheel source selection must be a boolean")
    if not isinstance(additional_plugins, tuple):
        raise ValueError("Additional frozen applications must be an immutable tuple")
    applications = (plugin, *additional_plugins)
    roots = tuple(sorted({
        package for application in applications for package in _package_roots(
            application, **({"closed_wheels": True} if closed_wheel_source else {}),
        )
    }))
    plugin = plugin.resolve(strict=True)
    common = Path(os.path.commonpath(roots))
    destination = destination.resolve()
    for root in roots:
        if destination == root or destination.is_relative_to(root):
            raise ValueError(f"Frozen input destination is inside runtime source: {destination}")
    progress: _ProgressOptions = {"on_progress": on_progress} if on_progress is not None else {}
    files = _source_input_files(roots, closed_wheel_source=closed_wheel_source, **progress)
    Path(_native_retained_path(destination)).mkdir(parents=True, exist_ok=False)
    try:
        buffer_limit = 32 * 1024 * 1024
        with tempfile.SpooledTemporaryFile(
            max_size=buffer_limit, dir=_native_retained_path(destination),
        ) as buffer:
            offsets: dict[Path, tuple[int, int]] = {}

            def retain_source(source: Path, data: bytes) -> None:
                if deadline is not None:
                    _check_deadline(deadline)
                offsets[source] = (buffer.tell(), len(data))
                if buffer.tell() + len(data) > buffer_limit:
                    buffer.rollover()
                if buffer.write(data) != len(data):
                    raise OSError("Frozen source buffer write was incomplete")

            before = runtime.fingerprint_source(files, on_file=retain_source, **progress)
            # Finish the full baseline before copying, preserving the mutation barrier.
            def retained_sources() -> Iterator[tuple[Path, bytes]]:
                for source in files:
                    if deadline is not None:
                        _check_deadline(deadline)
                    offset, length = offsets[source]
                    buffer.seek(offset)
                    data = buffer.read(length)
                    if len(data) != length:
                        raise OSError("Frozen source buffer read was incomplete")
                    yield source, data

            def write_source(item: tuple[Path, bytes]) -> None:
                source, data = item
                if deadline is not None:
                    _check_deadline(deadline)
                target = destination / source.relative_to(common)
                Path(_native_retained_path(target.parent)).mkdir(parents=True, exist_ok=True)
                Path(_native_retained_path(target)).write_bytes(data)
                shutil.copymode(
                    _native_retained_path(source), _native_retained_path(target),
                    follow_symlinks=False,
                )
                if deadline is not None:
                    _check_deadline(deadline)

            _bounded_content_map(
                write_source, retained_sources(),
                **({"on_result": lambda _result: on_progress()} if on_progress is not None else {}),
            )
        frozen_roots = tuple(destination / root.relative_to(common) for root in roots)
        frozen_plugin = destination / plugin.relative_to(common)
        frozen_applications = tuple(
            destination / application.resolve(strict=True).relative_to(common)
            for application in applications
        )
        if tuple(sorted({
            package for application in frozen_applications for package in _package_roots(
                application, **({"closed_wheels": True} if closed_wheel_source else {}),
            )
        })) != tuple(sorted(frozen_roots)):
            raise RuntimeError("Runtime dependency graph changed while freezing inputs")
        after_files = _source_input_files(roots, closed_wheel_source=closed_wheel_source, **progress)
        if after_files != files:
            changed = sorted(set(after_files).symmetric_difference(files))
            detail = ", ".join(str(path.relative_to(common)) for path in changed[:10])
            raise RuntimeError(f"Runtime input file set changed while freezing inputs: {detail}")
        after = runtime.fingerprint_source(files, **progress)
        native_roots = tuple(Path(_native_retained_path(root)) for root in frozen_roots)
        frozen_hash = runtime.fingerprint_source(_source_input_files(
            native_roots, closed_wheel_source=closed_wheel_source, **progress,
        ), **progress)
        if before != after or before != frozen_hash:
            raise RuntimeError("Runtime input content changed while freezing inputs")
        if deadline is not None:
            _check_deadline(deadline)
        return FrozenInputs(
            frozen_plugin, frozen_roots, frozen_hash,
            closed_wheel_source=closed_wheel_source,
        )
    except BaseException:
        shutil.rmtree(_native_retained_path(destination))
        raise


def _retained_inputs(
    inputs: FrozenInputs, uv: Path, python: Path, *, refresh: bool, timeout: float = 180,
) -> AbstractContextManager[FrozenInputs]:
    return retained_inputs(
        inputs, uv, python, refresh=refresh, timeout=timeout, engine=sys.modules[__name__],
    )


@contextmanager
def _retained_input_graph(inputs: FrozenInputs, uv: Path, python: Path, *, refresh: bool,
    on_progress: Callable[[], None],
) -> Iterator[FrozenInputs]:
    with retained_input_graph(
        inputs, uv, python, refresh=refresh, on_progress=on_progress,
        engine=sys.modules[__name__],
    ) as frozen:
        yield replace(frozen, diagnostics=inputs.diagnostics)


def _child(
    argv: list[str], *, inputs: FrozenInputs, descriptor: int | None,
    timeout: float, delays: tuple[float, ...] = (), transient: str = "",
    environment: dict[str, str] | None = None, cwd: Path | None = None,
    candidate: Path | None = None,
    on_progress: Callable[[], None] | None = None,
    allow_breakaway: bool = False,
) -> subprocess.CompletedProcess[str]:
    env = dict(os.environ if environment is None else environment)
    if environment is None:
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(name, None)
    procutil = _procutil(inputs)
    native_argv = argv
    if os.name == "nt" and Path(argv[0]).is_absolute():
        executable = _native_retained_path(argv[0])
        if Path(argv[0]).name.lower() == "powershell.exe":
            ordinary = runtime._normalize_windows_extended_path(argv[0])
            if Path(ordinary).is_absolute() and len(ordinary) < 260:
                executable = ordinary
        native_argv = [executable, *argv[1:]]
    for attempt in range(len(delays) + 1):
        decoders = {name: codecs.getincrementaldecoder("utf-8")(errors="replace")
                    for name in ("stdout", "stderr")}
        try:
            output_fds = {name: sys.stderr.fileno() for name in ("stdout", "stderr")}
        except (AttributeError, io.UnsupportedOperation):
            output_fds = {}

        def relay(name: str, data: bytes) -> None:
            if not output_fds:
                sys.stderr.write(decoders[name].decode(data))
                sys.stderr.flush()
            if on_progress is not None:
                on_progress()

        remaining = timeout
        if inputs.wheel_inputs is not None:
            _check_deadline(inputs.wheel_inputs.deadline)
            remaining = min(timeout, inputs.wheel_inputs.deadline - time.monotonic())
        try:
            result = run_owned(
                native_argv, cwd=cwd or Path(Path(argv[0]).resolve().anchor), env=env, timeout=remaining,
                procutil=procutil, construction_fd=descriptor,
                on_output=relay if on_progress is not None or not output_fds else None,
                output_fds=output_fds,
                allow_breakaway=allow_breakaway,
                **cast(_DiagnosticOptions, {
                    "diagnostics_path": inputs.plugin / "scripts" / "installer_diagnostics.py",
                } if getattr(inputs, "diagnostics", False) else {}),
                **cast(dict[str, Any], {
                    "runtime_root": candidate.parent.parent, "runtime_version": candidate.name,
                    "runtime_path": Path(runtime.__file__),
                } if candidate is not None else {}),
                **cast(dict[str, Any], {"deadline": inputs.wheel_inputs.deadline} if inputs.wheel_inputs is not None else {}),
            )
            if inputs.wheel_inputs is not None:
                _check_deadline(inputs.wheel_inputs.deadline)
            return result
        except subprocess.CalledProcessError as error:
            diagnostic = error.stdout + error.stderr
            if attempt == len(delays) or not re.search(transient, diagnostic):
                if diagnostic:
                    print(f"Installer child failed:\n{diagnostic}", file=sys.stderr, flush=True)
                raise
            print(
                f"Installer child hit a transient failure; retrying in {delays[attempt]}s:\n"
                f"{diagnostic}", file=sys.stderr, flush=True,
            )
            if inputs.wheel_inputs is not None and (
                inputs.wheel_inputs.deadline - time.monotonic() <= delays[attempt]
            ):
                _check_deadline(inputs.wheel_inputs.deadline)
                raise TimeoutError("closed wheel acquisition/assembly deadline would expire during retry")
            time.sleep(delays[attempt])
        except subprocess.TimeoutExpired as error:
            diagnostic = str(error.stdout or "") + str(error.stderr or "")
            print(
                f"Installer child timed out after {remaining}s:\n{diagnostic}",
                file=sys.stderr, flush=True,
            )
            raise
        finally:
            for decoder in decoders.values():
                sys.stderr.write(decoder.decode(b"", final=True))
            sys.stderr.flush()
    raise AssertionError("installer retry loop did not settle")


def _input_origins(inputs: FrozenInputs, slot: Path | None = None) -> dict[str, str]:
    if inputs.application is not None:
        if slot is None or inputs.wheel_inputs is None:
            raise ValueError("Application origins require the retained slot and complete wheels")
        return {
            name: _file_uri(slot / "wheels" / filename)
            for name, _version, filename, _digest in inputs.wheel_inputs.distributions
        }
    if inputs.source_wheels is not None:
        return inputs.source_wheels.origins()
    if isinstance(inputs.wheel_inputs, WheelReuseInputs):
        return {}
    if inputs.wheel_inputs is not None:
        return {
            name: (
                _file_uri(inputs.wheel_inputs.snapshot.paths[filename])
                + "#sha256=" + digest.removeprefix("sha256:")
            )
            for name, _version, filename, digest in inputs.wheel_inputs.distributions
        }
    return {
        tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]:
        _file_uri(root) for root in _installed_packages(inputs)
    }


def _verify_installed(
    inputs: FrozenInputs, slot: Path, python: Path | None, module: str,
    descriptor: int | None, timeout: float, *, health_callable: str | None = None,
    require_frozen_origin: bool = True,
    confirm_source_contents: bool = False,
) -> None:
    def run(argv: list[str]) -> subprocess.CompletedProcess[str]:
        return _child(
            argv, inputs=inputs, descriptor=descriptor, timeout=timeout, candidate=slot,
        )

    verify_installed_inputs(
        slot, python, module, origins=_input_origins(inputs, slot),
        extras=inputs.extras, local_packages=inputs.local_packages,
        excluded_packages=_excluded_packages(inputs), wheel_inputs=inputs.wheel_inputs,
        source_wheels=inputs.source_wheels, health_callable=health_callable,
        require_frozen_origin=require_frozen_origin,
        confirm_source_contents=confirm_source_contents, run=run,
    )


def _verify_application_wheels(inputs: FrozenInputs, slot: Path) -> None:
    assert inputs.wheel_inputs is not None
    verify_application_wheels(inputs.wheel_inputs, slot)


def _construct(
    inputs: FrozenInputs, slot: Path, uv: Path, base_python: Path,
    descriptor: int | None, timeout: float, *, refresh: bool = False, nested_environment: bool = False,
) -> Path:
    uv = Path(_select_uv_executable(str(Path.cwd() / uv))).resolve(strict=True)
    base_python = base_python.resolve(strict=True)
    if inputs.application is not None:
        if refresh or inputs.wheel_inputs is None or (inputs.application / ".venv").exists():
            raise ValueError("Application construction requires an unstaged immutable wheel project")
        declaration = inputs.application / "application.json"
        info = _ordinary(declaration)
        if info.st_size > 4 << 20:
            raise ValueError("Application construction declaration exceeds its bound")
        with _open(declaration) as stream:
            construction = json.load(stream).get("construction")
        if construction is not None:
            version = tomllib.loads(
                (inputs.plugin / "pyproject.toml").read_text(encoding="utf-8"),
            )["project"]["version"]
            identity = runtime_source.read_source_identity(inputs.plugin, version)
            expected = {
                "identity": runtime_source.manifest_fields(identity),
                "runtime_version": (
                    identity.runtime_version + ".profile." + inputs.wheel_inputs.receipt()["lock_id"][7:23]
                    if identity is not None else None
                ),
                "payload_hash": inputs.payload_hash,
                "source_hash": _construction_hash(
                    _source_input_files(
                        inputs.packages, closed_wheel_source=inputs.closed_wheel_source,
                    ), inputs.extras, inputs.local_packages,
                ),
                "wheel_inputs": inputs.wheel_inputs.receipt(),
                "extras": list(inputs.extras),
                "local_packages": list(inputs.local_packages) if inputs.local_packages is not None else None,
                "excluded_packages": _excluded_packages(inputs),
            }
            if construction != expected:
                raise ValueError("Application declaration differs from the retained actual Source/profile")
        files = _source_files((inputs.application,))
        before = runtime.fingerprint_source(files)
        copied = []
        for source in files:
            target = slot / source.relative_to(inputs.application)
            target.parent.mkdir(parents=True, exist_ok=True)
            info = _ordinary(source)
            with _open(source) as incoming, target.open("xb") as output:
                opened = _unchanged(source, info, incoming)
                _digest(incoming, info.st_size, inputs.wheel_inputs.deadline, destination=output)
                _unchanged(source, info, incoming, opened=opened)
            shutil.copymode(source, target, follow_symlinks=False)
            copied.append(target)
        if runtime.fingerprint_source(tuple(copied)) != before:
            raise RuntimeError("Application inputs changed while copying the slot project")
        _verify_application_wheels(inputs, slot)
        environment = _application_staging_environment(inputs.application, uv, base_python)
        command = (
            [
                str(Path(os.environ["SystemRoot"]) / "System32" / "WindowsPowerShell" / "v1.0"
                    / "powershell.exe"),
                "-NoProfile", "-NonInteractive", "-File", str(slot / "install.ps1"), "--stage",
            ] if os.name == "nt" else ["/bin/sh", str(slot / "install.sh"), "--stage"]
        )
        _child(
            command, inputs=inputs, descriptor=descriptor, timeout=timeout, candidate=slot,
            environment=environment,
        )
        if (
            _source_files((inputs.application,)) != files
            or runtime.fingerprint_source(files) != before
            or runtime.fingerprint_source(tuple(copied)) != before
            or runtime.fingerprint_source(_source_input_files(
                inputs.packages, closed_wheel_source=inputs.closed_wheel_source,
            )) != inputs.retained_hash
        ):
            raise RuntimeError("Application inputs changed during slot staging")
        _verify_application_wheels(inputs, slot)
        python = slot / ".venv" / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
        if not python.is_file() or not (slot / ".venv" / "pyvenv.cfg").is_file():
            raise RuntimeError("Application staging did not produce its slot-local interpreter")
        inputs.wheel_inputs.verify()
        return python
    environment_root = slot / ".venv" if nested_environment else slot
    python = environment_root / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    native_python = Path(_native_retained_path(python))
    configuration = Path(_native_retained_path(environment_root / "pyvenv.cfg"))
    if not refresh or not native_python.is_file() or not configuration.is_file():
        _child(
            [str(base_python), "-I", "-m", "venv", "--without-pip", "--copies", _native_retained_path(environment_root)],
            inputs=inputs, descriptor=descriptor, timeout=timeout, candidate=slot,
            delays=(0.75, 0.75) if os.name == "nt" else (),
            transient=r"Access is denied|failed to persist temporary file",
        )
    if not native_python.is_file() or not configuration.is_file():
        raise RuntimeError(f"Runtime construction did not produce a complete interpreter: {slot}")
    transaction = inputs.transaction
    if transaction is None:
        raise RuntimeError("Runtime construction requires the canonical retained UV transaction")
    if inputs.wheel_inputs is not None:
        inputs.wheel_inputs.verify()
    if inputs.source_wheels is not None:
        inputs.source_wheels.verify()
    _child(
        list(transaction.argv),
        inputs=inputs, descriptor=descriptor, timeout=timeout, candidate=slot,
        delays=(3, 6, 10), transient=r"SRE module mismatch",
        environment=transaction.environment, cwd=transaction.cwd,
    )
    if runtime.fingerprint_source(_source_input_files(
        inputs.packages, closed_wheel_source=inputs.closed_wheel_source,
    )) != inputs.retained_hash:
        raise RuntimeError("Frozen runtime inputs changed during package construction")
    if inputs.wheel_inputs is not None:
        inputs.wheel_inputs.verify()
    if inputs.source_wheels is not None:
        inputs.source_wheels.verify()
    return python


def _prepare_installed(
    inputs: FrozenInputs, slot: Path, python: Path, callable_name: str,
    data: dict[str, Any], descriptor: int | None, timeout: float, payload: FrozenInputs,
) -> None:
    module, separator, function = callable_name.partition(":")
    if not separator or not module or not function:
        raise ValueError("Construction callable must be an installed module:function")
    _child(
        [str(python), "-I", "-B", "-c", _PREPARE_INSTALLED_CHECK, str(slot), module, function,
         json.dumps({**data, "frozen_plugin": str(payload.plugin)}),
         hashlib.sha256(inputs.source_wheels.canonical).hexdigest()
         if inputs.source_wheels is not None else "", str(_native_retained_path(slot))],
        inputs=inputs, descriptor=descriptor, timeout=timeout, candidate=slot,
    )


def _refuse(decision: str, version: str) -> None:
    if decision == runtime.ADMIT_CONTENT_CONFLICT:
        raise ContentRepairRequired(
            f"Published runtime {version} has different content; publish a new version "
            "or use an explicitly owned dev slot. Force cannot rewrite a numbered runtime."
        )
    if decision == runtime.ADMIT_HEALTH_REPAIR_REQUIRED:
        raise HealthRepairRequired(
            f"Runtime {version} requires distinct-generation health repair; "
            "ordinary construction cannot replace its published or selected identity."
        )
    if decision not in (runtime.ADMIT_CONSTRUCT, runtime.ADMIT_REUSE):
        raise RuntimeError(f"Unrecognized runtime admission for {version}: {decision}")


def _assert_source_pin(plugin: Path, expected: dict[str, Any] | None) -> None:
    if expected is None:
        return
    package_version = tomllib.loads(
        (plugin / "pyproject.toml").read_text(encoding="utf-8"),
    )["project"]["version"]
    observed = runtime_source.read_source_identity(plugin, package_version)
    if runtime_source.manifest_fields(observed) != expected:
        raise RuntimeError("Source identity changed during retained runtime construction")


def numbered_candidate(
    root: Path, version: str, *, plugin: Path, uv: Path, base_python: Path,
    health_module: str, health_callable: str | None = None,
    validate_candidate: Callable[[Path], None] | None = None,
    construction_callable: str | None = None, construction_data: dict[str, Any] | None = None,
    process_retention: bool = False,
    reuse_generations: bool = False,
    expected_source_identity: dict[str, Any] | None = None,
    extras: tuple[str, ...] = (),
    local_packages: tuple[str, ...] | None = None,
    wheel_inputs: WheelInstallInputs | WheelReuseInputs | None = None,
    application: Path | None = None,
    closed_wheel_source: bool = False,
    nested_environment: bool = False,
    source_wheel_cache_root: Path | None = None,
    expected_payload_hash: str | None = None,
    wait_timeout: float = 180, child_timeout: float = 180,
    progress: Callable[[str], None] | None = None,
    diagnostics: bool = False,
) -> AbstractContextManager[ReadyCandidate]:
    """Construct or reuse an immutable identity; lifecycle owns cutover/governance.

    A prior freeze's expected payload hash avoids another initial scan, not
    retained-input validation before construction or source validation on reuse.
    """
    if version == "dev":
        raise ValueError("Mutable dev construction requires its explicit owning lifecycle")
    return _candidate(
        root, version, plugin=plugin, uv=uv, base_python=base_python,
        health_module=health_module, health_callable=health_callable,
        validate_candidate=validate_candidate,
        construction_callable=construction_callable, construction_data=construction_data,
        process_retention=process_retention, reuse_generations=reuse_generations,
        expected_source_identity=expected_source_identity,
        extras=extras,
        local_packages=local_packages,
        wheel_inputs=wheel_inputs,
        application=application,
        closed_wheel_source=closed_wheel_source,
        nested_environment=nested_environment,
        source_wheel_cache_root=source_wheel_cache_root,
        expected_payload_hash=expected_payload_hash,
        diagnostics=diagnostics,
        wait_timeout=wait_timeout, child_timeout=child_timeout,
        progress=progress,
    )


def dev_candidate(root: Path, *, owner: str, **kwargs: Any) -> AbstractContextManager[ReadyCandidate]:
    """Construct or reuse immutable source content under its explicit dev claim."""
    return _candidate(root, runtime.DEV_VERSION, dev_owner=owner, **kwargs)


_check_dev_owner = partial(check_dev_owner, runtime)
_revalidate_input_content = partial(revalidate_input_content, sys.modules[__name__])


def _lease_timeout(inputs: FrozenInputs, timeout: float) -> float:
    if inputs.wheel_inputs is None:
        return timeout
    _check_deadline(inputs.wheel_inputs.deadline)
    return max(0, min(timeout, inputs.wheel_inputs.deadline - time.monotonic()))


@contextmanager
def _candidate(
    root: Path, version: str, *, plugin: Path, uv: Path, base_python: Path,
    health_module: str, health_callable: str | None = None,
    validate_candidate: Callable[[Path], None] | None = None,
    construction_callable: str | None = None, construction_data: dict[str, Any] | None = None,
    process_retention: bool = False, dev_owner: str | None = None,
    reuse_generations: bool = False,
    expected_source_identity: dict[str, Any] | None = None,
    extras: tuple[str, ...] = (),
    local_packages: tuple[str, ...] | None = None,
    wheel_inputs: WheelInstallInputs | WheelReuseInputs | None = None,
    application: Path | None = None,
    closed_wheel_source: bool = False,
    nested_environment: bool = False,
    source_wheel_cache_root: Path | None = None,
    expected_payload_hash: str | None = None,
    wait_timeout: float = 180, child_timeout: float = 180,
    progress: Callable[[str], None] | None = None,
    diagnostics: bool = False,
) -> Iterator[ReadyCandidate]:
    if expected_payload_hash is not None and not re.fullmatch(r"[0-9a-f]{64}", expected_payload_hash):
        raise ValueError("Expected runtime payload hash must be a full source SHA256")

    def report(phase: str) -> None:
        if progress is not None:
            progress(phase)

    report("source-admission")
    root = root.resolve()
    if type(closed_wheel_source) is not bool or closed_wheel_source and (
        dev_owner is not None or not isinstance(wheel_inputs, (WheelInstallInputs, WheelReuseInputs))
    ):
        raise ValueError("Closed-wheel source construction requires immutable inspected wheels")
    if type(nested_environment) is not bool or (nested_environment and (
        application is not None or not isinstance(wheel_inputs, (WheelInstallInputs, WheelReuseInputs))
    )):
        raise ValueError("Nested native construction requires closed wheels without an application project")
    if application is not None and (
        dev_owner is not None or not isinstance(wheel_inputs, (WheelInstallInputs, WheelReuseInputs))
    ):
        raise ValueError("Application construction requires immutable inspected wheel inputs")
    _assert_source_pin(plugin, expected_source_identity)
    extras = _selected_extras(plugin, extras)
    def installed_python(slot: Path) -> Path | None:
        python = runtime.slot_python(root, slot.name)
        if nested_environment and (
            python is None or python.parent.parent != slot / ".venv"
        ):
            raise HealthRepairRequired("Native construction requires its selected nested environment layout")
        return python
    if dev_owner is not None and (extras or local_packages is not None or wheel_inputs is not None):
        raise ValueError("Dependency profiles require an immutable numbered runtime")
    packages = _package_roots(plugin, **({"closed_wheels": True} if closed_wheel_source else {}))
    if wheel_inputs is not None and local_packages == ():
        if extras:
            raise ValueError("A third-party-only wheel closure cannot select local extras")
    else:
        local_packages = _selected_local_packages(plugin, packages, local_packages)
    if wheel_inputs is not None:
        if not isinstance(wheel_inputs, (WheelInstallInputs, WheelReuseInputs)):
            raise ValueError("Closed construction requires inspected wheel inputs")
        wheel_inputs.verify()
        if isinstance(wheel_inputs, WheelReuseInputs) and (
            reuse_generations or wheel_inputs.generation != version
        ):
            raise ValueError("Existing wheel evidence requires its exact immutable generation")
        included = {_package_name(package) for package in packages}
        selected_packages = set(local_packages) if local_packages is not None else included
        installed = {name for name, _version, _filename, _digest in wheel_inputs.distributions}
        if not selected_packages <= installed or (included - selected_packages) & installed:
            raise ValueError("Inspected wheels differ from the selected local dependency graph")
    check_construction(
        root, wheel_inputs.receipt() if wheel_inputs is not None else None,
        core=_package_name(plugin),
    )
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    (root / ".installer-inputs").mkdir(parents=True, exist_ok=True)
    report("source-fingerprint")
    with _source_phase("source-selection", child_timeout, {}) as source_progress:
        def selected() -> None:
            _source_deadline(source_progress)
            source_progress.renew()

        inputs = FrozenInputs(
            plugin.resolve(strict=True), packages,
            expected_payload_hash if expected_payload_hash is not None else _wheel_construction_hash(
                _construction_hash(
                    _source_input_files(
                        packages, closed_wheel_source=closed_wheel_source, on_progress=selected,
                    ), extras, local_packages,
                    on_progress=selected,
                ), wheel_inputs,
            ),
            extras=extras, local_packages=local_packages, wheel_inputs=wheel_inputs,
            application=application,
            closed_wheel_source=closed_wheel_source,
            diagnostics=diagnostics,
        )
    report("source-fingerprint-ready")
    with ExitStack() as retained:
        if dev_owner is not None:
            _check_dev_owner(root, dev_owner)
            version = f"dev-{inputs.payload_hash}"
        if reuse_generations:
            if dev_owner is not None or runtime.package_version(version) != version:
                raise ValueError("Generation discovery requires an ordinary package version")
            versions = dict.fromkeys([
                runtime.current_version(root), runtime.read_last_known_good(root),
                *reversed(runtime.list_versions(root)),
            ])
            for generation in versions:
                if (
                    generation and generation != runtime.DEV_VERSION
                    and runtime.package_version(generation) == version
                    and runtime.is_complete(root, generation, expect_hash=inputs.payload_hash)
                ):
                    version = generation
                    break
        decision = runtime.check_admission(root, version, payload_hash=inputs.payload_hash)
        _refuse(decision, version)
        if isinstance(wheel_inputs, WheelReuseInputs) and decision != runtime.ADMIT_REUSE:
            raise HealthRepairRequired("Existing wheel evidence cannot construct a missing runtime")
        if decision == runtime.ADMIT_REUSE:
            report("reuse-retention")
            with runtime.retention_custody(
                root, version, payload_hash=inputs.payload_hash,
                timeout=_lease_timeout(inputs, wait_timeout),
            ) as (slot, descriptor):
                if expected_payload_hash is not None:
                    _revalidate_input_content(inputs)
                _assert_source_pin(plugin, expected_source_identity)
                report("reuse-health")
                _verify_installed(
                    inputs, slot, installed_python(slot), health_module,
                    descriptor, child_timeout, health_callable=health_callable,
                    require_frozen_origin=False,
                )
                if validate_candidate is not None:
                    validate_candidate(slot)
                _assert_source_pin(plugin, expected_source_identity)
                if dev_owner is not None:
                    _check_dev_owner(root, dev_owner)
                check_construction(
                    root, wheel_inputs.receipt() if wheel_inputs is not None else None,
                    core=_package_name(plugin),
                )
                yield ReadyCandidate(
                    slot, inputs.payload_hash, True, descriptor, inputs.plugin, inputs.extras,
                    inputs.local_packages,
                )
            return
        with ExitStack() as construction:
            report("construction-lease")
            with runtime.construction_lease(
                root, version, timeout=_lease_timeout(inputs, wait_timeout),
            ) as descriptor:
                report("construction-lease-acquired")
                _assert_source_pin(plugin, expected_source_identity)
                if dev_owner is not None:
                    _check_dev_owner(root, dev_owner)
                decision = runtime.check_admission(root, version, payload_hash=inputs.payload_hash)
                _refuse(decision, version)
                reused = decision == runtime.ADMIT_REUSE
                if not reused:
                    slot = runtime.slot(root, version)
                    environment = slot / ".venv" if application is not None or nested_environment else slot
                    python = environment / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    if construction_callable is not None:
                        # UV's non-editable metadata projection is a build input,
                        # not the selected source payload copied by preparation.
                        temporary = retained.enter_context(_temporary_inputs(None))
                        with _source_phase("payload-snapshot", child_timeout, {}) as source_progress:
                            deadline = _source_deadline(source_progress)
                            if inputs.wheel_inputs is not None:
                                deadline = min(deadline, inputs.wheel_inputs.deadline)
                            frozen = freeze_inputs(
                                inputs.plugin, _private_retained_root(Path(temporary)) / "source",
                                deadline=deadline, on_progress=source_progress.renew,
                                closed_wheel_source=inputs.closed_wheel_source,
                            )
                        payload = replace(inputs, plugin=frozen.plugin, packages=frozen.packages)
                        _revalidate_input_content(payload)
                        _assert_source_pin(payload.plugin, expected_source_identity)
                    report("retain-uv-inputs")
                    inputs = retained.enter_context(_retained_inputs(
                        inputs, uv, python, refresh=False, timeout=child_timeout,
                    ))
                    report("retained-uv-inputs-ready")
                    if wheel_inputs is None:
                        def run_source_build(argv: list[str], **options: Any) -> subprocess.CompletedProcess[str]:
                            return _child(
                                argv, inputs=inputs, descriptor=descriptor,
                                timeout=min(child_timeout, options.pop("timeout", child_timeout)),
                                candidate=slot, **options,
                            )

                        cache_root = root if source_wheel_cache_root is None else source_wheel_cache_root
                        inputs = retained.enter_context(retained_source_wheels(
                            inputs, cache_root, uv, base_python,
                            run=run_source_build, timeout=child_timeout,
                        ))
                    report("construct-runtime")
                    python = _construct(
                        inputs, slot, uv, base_python, descriptor, child_timeout,
                        **({"nested_environment": True} if nested_environment else {}),
                    )
                    report("constructed-runtime")
                    origin_proof = Path(_native_retained_path(slot / ".runtime-input-origins.json"))
                    if origin_proof.is_symlink():
                        raise RuntimeError("Frozen installation origin evidence cannot be a symlink")
                    origin_proof.write_text(json.dumps({
                        "schema": "installer-engine.frozen-input-origins",
                        "version": 2 if inputs.wheel_inputs is not None else 1,
                        "packages": _input_origins(inputs, slot),
                        **({"wheel_inputs": inputs.wheel_inputs.receipt()}
                           if inputs.wheel_inputs is not None else {}),
                        **({"source_wheels": inputs.source_wheels.receipt()}
                           if inputs.source_wheels is not None else {}),
                        **({"extras": list(inputs.extras)} if inputs.extras else {}),
                        **({"local_packages": list(inputs.local_packages)}
                           if inputs.local_packages is not None else {}),
                    }, indent=2) + "\n", encoding="utf-8")
                    if process_retention:
                        bootstrap = Path(_native_retained_path(slot / ".runtime-bootstrap"))
                        if bootstrap.is_symlink():
                            raise RuntimeError("Runtime bootstrap cannot be a symlink")
                        bootstrap.mkdir()
                        for name in (
                            "runtime_launcher.py", "versioned_runtime.py",
                            "versioned_runtime_leases.py", "versioned_runtime_source.py",
                            "versioned_runtime_identity.py", "versioned_runtime_fingerprint.py",
                            "versioned_runtime_construction.py",
                            "versioned_runtime_publication.py",
                            "versioned_runtime_cache.py", "daemon_instance_runtime.pyz",
                        ):
                            source = (
                                slot / "runtime" if inputs.application is not None
                                else inputs.plugin / "scripts"
                            ) / name
                            shutil.copyfile(source, bootstrap / name)
                    if construction_callable is not None:
                        _revalidate_input_content(payload)
                        _assert_source_pin(payload.plugin, expected_source_identity)
                        report("prepare-installed-runtime")
                        _prepare_installed(
                            inputs, slot, python, construction_callable,
                            construction_data or {}, descriptor, child_timeout, payload,
                        )
                        _revalidate_input_content(payload)
                        _assert_source_pin(payload.plugin, expected_source_identity)
                    report("verify-installed-runtime")
                    _verify_installed(
                        inputs, slot, python, health_module, descriptor, child_timeout,
                        health_callable=health_callable,
                        confirm_source_contents=construction_callable is None,
                    )
                    if inputs.wheel_inputs is not None:
                        inputs.wheel_inputs.verify()
                    if inputs.source_wheels is not None:
                        inputs.source_wheels.verify()
                    if validate_candidate is not None:
                        validate_candidate(slot)
                    _assert_source_pin(plugin, expected_source_identity)
                    if dev_owner is not None:
                        _check_dev_owner(root, dev_owner)
                    runtime.mark_complete(
                        root, version, payload_hash=inputs.payload_hash, dev_owner=dev_owner,
                    )
                    report("runtime-complete")
                # Transfer to retention while construction is still owned. The
                # caller never joins cutover coordination holding construction.
                slot, retention_descriptor = construction.enter_context(
                    runtime.retention_custody(
                        root, version, payload_hash=inputs.payload_hash,
                        timeout=_lease_timeout(inputs, wait_timeout),
                    )
                )
            if reused:
                if expected_payload_hash is not None:
                    _revalidate_input_content(inputs)
                _verify_installed(
                    inputs, slot, installed_python(slot), health_module,
                    retention_descriptor, child_timeout, health_callable=health_callable,
                    require_frozen_origin=False,
                )
                if validate_candidate is not None:
                    validate_candidate(slot)
            _assert_source_pin(plugin, expected_source_identity)
            if dev_owner is not None:
                _check_dev_owner(root, dev_owner)
            check_construction(
                root, wheel_inputs.receipt() if wheel_inputs is not None else None,
                core=_package_name(plugin),
            )
            report("candidate-retained")
            yield ReadyCandidate(
                slot, inputs.payload_hash, reused, retention_descriptor, inputs.plugin, inputs.extras,
                inputs.local_packages,
            )
