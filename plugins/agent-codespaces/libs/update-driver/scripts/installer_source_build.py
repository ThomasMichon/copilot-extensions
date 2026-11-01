"""Content-bound local wheels for the ordinary retained-source constructor.

Native UV still admits build dependencies through the caller's retained settings.
The installed build tools, not an open requirement or a backend version alone,
identify subsequent offline builds. Unsupported build contracts retain the
ordinary isolated-build path.
"""
from __future__ import annotations

from collections.abc import Callable, Iterator
from contextlib import contextmanager
from concurrent.futures import ThreadPoolExecutor
from dataclasses import dataclass, replace
import ast
import hashlib
import json
import math
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import tomllib
from types import ModuleType
from typing import TYPE_CHECKING

from installer_dependency_policy import (
    _argument_pairs, _native_retained_path, _private_retained_root, _temporary_inputs,
    _write_toml, retain_uv_install,
)
from installer_source_wheel_cache import cached_source_wheel
from installer_wheel_inputs import (
    WheelDeadlineError, WheelError, WheelLimits, _check_deadline, inspect_archive,
)
from installer_wheel_snapshot import _digest, _open, _ordinary, _unchanged

if TYPE_CHECKING:
    from installer_engine import FrozenInputs


@contextmanager
def retained_input_graph(
    inputs: FrozenInputs, uv: Path, python: Path, *, refresh: bool,
    on_progress: Callable[[], None], engine: ModuleType,
) -> Iterator[FrozenInputs]:
    if isinstance(inputs.wheel_inputs, engine.WheelReuseInputs):
        raise ValueError("Existing wheel evidence cannot construct a runtime")
    if inputs.wheel_inputs is not None:
        if refresh:
            raise ValueError("Closed wheels require immutable numbered construction")
        with engine._temporary_inputs(None) as temporary:
            root = engine._private_retained_root(Path(temporary)).resolve()
            frozen = engine.freeze_inputs(
                inputs.plugin, root / "source", deadline=inputs.wheel_inputs.deadline,
                on_progress=on_progress,
            )
            if engine._wheel_construction_hash(
                engine._construction_hash(
                    engine._source_files(frozen.packages, on_progress=on_progress), inputs.extras,
                    inputs.local_packages, on_progress=on_progress,
                ),
                inputs.wheel_inputs,
            ) != inputs.payload_hash:
                raise RuntimeError("Runtime source changed before closed wheel construction")
            with engine.retain_closed_wheel_install(
                str(uv), inputs.wheel_inputs.snapshot, python=python,
            ) as transaction:
                yield engine.FrozenInputs(
                    frozen.plugin, frozen.packages, inputs.payload_hash, transaction,
                    frozen.payload_hash, inputs.extras, inputs.local_packages, inputs.wheel_inputs,
                )
        return
    files = engine._source_files(inputs.packages, on_progress=on_progress)
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    selected = engine._installed_packages(inputs)
    with engine.retain_uv_install(
        str(Path(engine._select_uv_executable(str(Path.cwd() / uv))).resolve(strict=True)),
        ["--python", str(python), "--link-mode", "copy" if refresh else "hardlink",
         *(["--reinstall"] if refresh else []),
         *(argument for package in selected
           for argument in ("-r", str(package / "pyproject.toml"))),
         *(str(package) + (
             f"[{','.join(inputs.extras)}]" if package == inputs.plugin and inputs.extras else ""
         ) for package in selected)],
        env=environment, cwd=inputs.plugin,
        trusted_source_roots=inputs.packages,
        non_editable=True,
        excluded_local_packages=tuple(engine._excluded_packages(inputs)),
        on_progress=on_progress,
    ) as transaction:
        if set(transaction.sources) != set(inputs.packages):
            raise RuntimeError("Retained UV inputs differ from the runtime dependency graph")
        if (
            engine._package_roots(inputs.plugin) != inputs.packages
            or engine._source_files(inputs.packages, on_progress=on_progress) != files
            or engine._construction_hash(
                files, inputs.extras, inputs.local_packages, on_progress=on_progress,
            ) != inputs.payload_hash
        ):
            raise RuntimeError("Runtime inputs changed while retaining UV inputs")
        if not set(files) <= set(transaction.source_hashes):
            raise RuntimeError("Retained UV inputs do not cover the runtime source files")
        changed = []
        for path in files:
            if hashlib.sha256(path.read_bytes()).hexdigest() != transaction.source_hashes[path]:
                changed.append(path)
            else:
                on_progress()
        if changed:
            raise RuntimeError(
                "Retained UV inputs differ from the admitted source content: "
                + ", ".join(str(path) for path in changed[:10])
            )
        packages = tuple(transaction.sources[path] for path in inputs.packages)
        plugin = transaction.sources[inputs.plugin]
        yield engine.FrozenInputs(
            plugin, packages, inputs.payload_hash, transaction,
            engine.runtime.fingerprint_source(
                engine._source_files(packages, on_progress=on_progress), on_progress=on_progress,
            ),
            inputs.extras,
            inputs.local_packages,
        )


_LIMITS = WheelLimits(64 << 20, 256 << 20, 64 << 20, 4 << 20, 20_000, 100, 1 << 20)
_MAX_SOURCE_BYTES = 256 << 20
_MAX_SOURCE_FILES = 20_000
_BUILD_CONFIG = {
    "config-settings", "build-constraint-dependencies", "extra-build-dependencies",
    "extra-build-variables", "no-build", "no-build-package", "no-binary",
    "no-binary-package", "no-build-isolation", "no-build-isolation-package",
    "no-cache", "refresh", "refresh-package", "reinstall", "reinstall-package",
}
_BACKEND_REQUIREMENTS = """
import contextlib, importlib, io, json
with contextlib.redirect_stdout(io.StringIO()):
    backend = importlib.import_module("setuptools.build_meta")
    hook = getattr(backend, "get_requires_for_build_wheel", None)
    requirements = hook({}) if hook is not None else []
print(json.dumps(requirements))
"""


class _AdditionalBuildRequirements(RuntimeError):
    pass


@dataclass(frozen=True)
class SourceWheelInputs:
    canonical: bytes
    archives: tuple[Path, ...]
    io_timeout: float

    def receipt(self) -> dict:
        return json.loads(self.canonical)

    def verify(self, *, deadline: float | _SourceProgress | None = None) -> None:
        started = time.monotonic()
        progress = (
            deadline if isinstance(deadline, _SourceProgress)
            else _SourceProgress("verification", self.io_timeout, started + self.io_timeout)
        )
        records = {record["filename"]: record for record in self.receipt().values()}
        if {path.name for path in self.archives} != records.keys():
            raise WheelError("source wheel archives differ from their construction")
        for path in self.archives:
            selected = _source_deadline(progress)
            if deadline is not None and not isinstance(deadline, _SourceProgress):
                selected = min(selected, _source_deadline(deadline))
            if _digest_file(path, _LIMITS.max_archive_bytes, selected) != records[path.name]["sha256"]:
                raise WheelError("retained source wheel changed during installation")
            progress.renew()

    def origins(self) -> dict:
        paths = {path.name: path for path in self.archives}
        return {
            name: paths[record["filename"]].as_uri() + "#"
            + record["sha256"].replace(":", "=", 1)
            for name, record in self.receipt().items()
        }


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


@dataclass
class _SourceProgress:
    name: str
    timeout: float
    deadline: float
    units: int = 0
    reported_at: float | None = None

    def renew(self) -> None:
        now = time.monotonic()
        self.deadline = now + self.timeout
        self.units += 1
        if self.reported_at is not None and now - self.reported_at < min(1.0, self.timeout / 4):
            print(".", end="", file=sys.stderr, flush=True)
            return
        if self.reported_at is not None:
            print(file=sys.stderr)
        self.reported_at = now
        print(
            f"Source wheel progress: phase={self.name} completed-units={self.units}",
            file=sys.stderr, flush=True,
        )


def _source_deadline(deadline: float | _SourceProgress) -> float:
    selected = deadline.deadline if isinstance(deadline, _SourceProgress) else deadline
    _check_deadline(selected)
    return selected


def _source_progress(deadline: float | _SourceProgress) -> None:
    if isinstance(deadline, _SourceProgress):
        deadline.renew()


def _digest_file(path: Path, limit: int, deadline: float | _SourceProgress, *, before=None) -> str:
    if before is None:
        before = _ordinary(path)
    with _open(path) as stream:
        opened = _unchanged(path, before, stream)
        digest, _size = _digest(stream, limit, _source_deadline(deadline))
        _unchanged(path, before, stream, opened=opened)
    return digest


def _ignored_source(relative: Path, excluded: set[str]) -> bool:
    name = relative.name
    return relative.as_posix() in excluded or name in {
        ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
        ".test-runs", "build", "dist", "node_modules",
    } or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))


def _manifest_digest(path: Path, limit: int, deadline: float | _SourceProgress, *, before=None) -> str:
    if before is None:
        before = _ordinary(path)
    if before.st_size > limit:
        raise WheelError("source wheel manifest exceeds its byte budget")
    _source_deadline(deadline)
    with _open(path) as stream:
        opened = _unchanged(path, before, stream)
        content = stream.read(before.st_size + 1)
        _unchanged(path, before, stream, opened=opened)
    data = tomllib.loads(content.decode("utf-8"))
    data.get("tool", {}).get("uv", {}).pop("sources", None)
    return "sha256:" + hashlib.sha256(_canonical(data)).hexdigest()


def _tree(
    root: Path, deadline: float | _SourceProgress, *,
    installed: bool = False, excluded: set[str] | None = None,
) -> dict:
    excluded = excluded or set()
    files, total = [], 0
    for directory, children, names in os.walk(root, followlinks=False):
        for name in list(children):
            path = Path(directory) / name
            if not installed and _ignored_source(path.relative_to(root), excluded):
                children.remove(name)
                continue
            info = path.lstat()
            if not stat.S_ISDIR(info.st_mode) or (
                getattr(info, "st_file_attributes", 0)
                & getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0)
            ):
                raise WheelError("source wheel inputs contain a linked directory")
            if name == "__pycache__":
                children.remove(name)
        for name in names:
            _source_deadline(deadline)
            path = Path(directory) / name
            relative = path.relative_to(root)
            if name.endswith((".pyc", ".pyo")) or (
                not installed and _ignored_source(relative, excluded)
            ):
                continue
            info = _ordinary(path)
            if len(files) >= _MAX_SOURCE_FILES or info.st_size > _MAX_SOURCE_BYTES - total:
                raise WheelError("source wheel input tree exceeds its byte or file budget")
            files.append((path, relative, info))
            total += info.st_size
            _source_progress(deadline)

    def read(item):
        path, relative, info = item
        digest = (
            _manifest_digest(path, info.st_size, deadline, before=info)
            if not installed and relative == Path("pyproject.toml")
            else _digest_file(path, info.st_size, deadline, before=info)
        )
        return relative.as_posix(), (digest, stat.S_IMODE(info.st_mode))

    result = {}
    with ThreadPoolExecutor(max_workers=4, thread_name_prefix="source-wheel-input") as pool:
        for offset in range(0, len(files), 4):
            _source_deadline(deadline)
            result.update(pool.map(read, files[offset:offset + 4]))
            _source_progress(deadline)
    return result


def _static_setup(package: Path) -> bool:
    path = package / "setup.py"
    if not path.exists():
        return True
    if _ordinary(path).st_size > 64 << 10:
        return False
    try:
        body = ast.parse(path.read_text(encoding="utf-8")).body
    except (SyntaxError, UnicodeError):
        return False
    if body and isinstance(body[0], ast.Expr) and isinstance(body[0].value, ast.Constant) and (
        isinstance(body[0].value.value, str)
    ):
        body = body[1:]
    return ast.dump(ast.Module(body=body, type_ignores=[])) == ast.dump(ast.parse(
        "from setuptools import setup\nsetup()\n",
    ))


def _eligibility(inputs) -> tuple[list[dict], str | None]:
    transaction = inputs.transaction
    environment = transaction.environment
    if any(
        name.upper().startswith(("UV_BUILD_", "UV_NO_BUILD", "UV_NO_BINARY", "UV_CONFIG_SETTINGS"))
        or name.upper() in {
            "UV_CONSTRAINT", "UV_OVERRIDE", "UV_REQUIREMENT", "UV_NO_CACHE",
            "UV_REFRESH", "UV_REFRESH_PACKAGE", "UV_REINSTALL", "UV_REINSTALL_PACKAGE",
        }
        for name in environment
    ):
        return [], "custom-build-environment"
    config = tomllib.loads(Path(environment["UV_CONFIG_FILE"]).read_text(encoding="utf-8"))
    if _BUILD_CONFIG.intersection(config) or _BUILD_CONFIG.intersection(config.get("pip", {})):
        return [], "custom-build-configuration"
    manifests = []
    for package in inputs.packages:
        data = tomllib.loads((package / "pyproject.toml").read_text(encoding="utf-8"))
        name = re.sub(r"[-_.]+", "-", data["project"]["name"]).lower()
        uv_settings = data.get("tool", {}).get("uv", {})
        if _BUILD_CONFIG.intersection(uv_settings) or _BUILD_CONFIG.intersection(uv_settings.get("pip", {})):
            return [], name + ":custom-build-configuration"
        for source in uv_settings.get("sources", {}).values():
            path = source.get("path") if isinstance(source, dict) else None
            if not isinstance(path, str) or (package / path).resolve() not in inputs.packages:
                return [], name + ":non-project-source-mapping"
        build = data.get("build-system", {})
        project = data.get("project", {})
        settings = data.get("tool", {}).get("setuptools", {})
        find = settings.get("packages", {})
        find = find.get("find", {}) if isinstance(find, dict) else {}
        if (
            build.get("build-backend") != "setuptools.build_meta" or "backend-path" in build
            or not isinstance(build.get("requires"), list) or not build["requires"]
            or any(not isinstance(value, str) or not value for value in build["requires"])
            or any(re.match(r"\s*(setuptools|wheel)(?:\s|[<>=!~;\[]|$)", value, re.I) is None
                   for value in build["requires"])
            or project.get("dynamic") or settings.get("dynamic") or settings.get("ext-modules")
            or settings.get("data-files") or settings.get("script-files") or settings.get("cmdclass")
            or (package / "MANIFEST.in").exists()
            or settings.get("package-dir") not in (None, {"": "src"})
            or (find and find.get("where", ["src"]) != ["src"])
            or not _static_setup(package)
            or (package / "setup.cfg").exists() or not (package / "src").is_dir()
        ):
            return [], name + ":non-static-setuptools-contract"
        manifests.append(data)
    return manifests, None


def _compact(
    package: Path, packages: tuple[Path, ...], output: Path, deadline: float | _SourceProgress,
) -> dict:
    """Remove nested vendored projects, not files belonging to this project."""
    excluded = {
        path.relative_to(package).as_posix()
        for path in packages if path != package and path.is_relative_to(package)
    }
    native_package = Path(_native_retained_path(package))
    count, total = 0, 0

    def ignore(directory, names):
        ignored = []
        for name in names:
            path = Path(directory) / name
            if _ignored_source(path.relative_to(native_package), excluded):
                ignored.append(name)
            elif getattr(path.lstat(), "st_file_attributes", 0) & 0x400 or path.is_symlink():
                raise WheelError("compact source inputs must not be linked")
        return ignored

    def copy(source, destination):
        nonlocal count, total
        path = Path(source)
        before = _ordinary(path)
        if count >= _MAX_SOURCE_FILES or before.st_size > _MAX_SOURCE_BYTES - total:
            raise WheelError("compact source exceeds its byte or file budget")
        with _open(path) as incoming, Path(destination).open("xb") as target:
            opened = _unchanged(path, before, incoming)
            _digest(incoming, _MAX_SOURCE_BYTES - total, _source_deadline(deadline), destination=target)
            _unchanged(path, before, incoming, opened=opened)
        shutil.copymode(source, destination)
        count += 1
        total += before.st_size
        _source_progress(deadline)
        return destination

    _source_deadline(deadline)
    shutil.copytree(
        _native_retained_path(package), _native_retained_path(output), ignore=ignore, copy_function=copy,
    )
    manifest = output / "pyproject.toml"
    mode = stat.S_IMODE(manifest.stat().st_mode)
    data = tomllib.loads(manifest.read_text(encoding="utf-8"))
    # UV has already retained and admitted the local graph. Builds use only the
    # individual static project; temporary graph-remapping paths are not inputs.
    data.get("tool", {}).get("uv", {}).pop("sources", None)
    _write_toml(manifest, data)
    manifest.chmod(mode)
    return _tree(output, deadline)


def _tool_tree(root: Path, deadline: float | _SourceProgress) -> dict:
    return _tree(root, deadline, installed=True)


def _wheel_record(path: Path, name: str, deadline: float | _SourceProgress) -> dict:
    with _open(path) as stream:
        facts = inspect_archive(stream, limits=_LIMITS, deadline=_source_deadline(deadline))
    if (
        re.sub(r"[-_.]+", "-", facts.name).lower() != name or facts.tags != ("py3-none-any",)
        or facts.wheel_metadata["Root-Is-Purelib"] != "true"
        or any(".data/" in member for member in facts.member_hashes)
    ):
        raise WheelError("cached source build did not produce its ordinary pure-Python wheel")
    result = {
        "filename": path.name, "version": facts.version,
        "sha256": _digest_file(path, _LIMITS.max_archive_bytes, deadline),
        "files": {
            member: digest for member, digest in facts.member_hashes.items()
            if member != facts.prefix + "RECORD"
        },
    }
    _source_progress(deadline)
    return result


@contextmanager
def _source_phase(name: str, timeout: float, elapsed: dict[str, float]):
    if type(timeout) not in (int, float) or not math.isfinite(timeout) or timeout <= 0:
        raise WheelError(f"source wheel phase={name} requires a positive finite process allowance")
    started = time.monotonic()
    progress = _SourceProgress(name, timeout, started + timeout)
    state = "failed"
    print(f"Source wheel phase: phase={name} state=starting", file=sys.stderr, flush=True)
    try:
        yield progress
        _source_deadline(progress)
        state = "completed"
    except WheelDeadlineError as error:
        raise WheelDeadlineError(f"source wheel phase={name} deadline exhausted") from error
    except _AdditionalBuildRequirements:
        state = "unsupported"
        raise
    finally:
        elapsed[name] = time.monotonic() - started
        print(
            f"\nSource wheel phase: phase={name} state={state} elapsed={elapsed[name]:.3f}s "
            f"completed-units={progress.units}",
            file=sys.stderr, flush=True,
        )


@contextmanager
def retained_source_wheels(inputs, root: Path, uv: Path, base_python: Path, *, run, timeout: float):
    """Substitute only fully built, content-bound local archives in a held install.

    ``run`` is the constructor's owned-child runner, already bound to its
    construction descriptor. This helper never acquires publication authority
    or changes a selected generation.
    """
    started = time.monotonic()
    manifests, reason = _eligibility(inputs)
    if reason is not None:
        print(
            f"Source wheel cache miss: {reason}; retaining isolated source builds",
            file=sys.stderr, flush=True,
        )
        yield inputs
        return
    runner = run

    def run(argv, **options):
        result = runner(
            argv, timeout=_source_deadline(progress) - time.monotonic(),
            on_progress=progress.renew, **options,
        )
        progress.renew()
        return result

    transaction = inputs.transaction
    uv = Path(transaction.argv[0]).resolve(strict=True)
    base_python = base_python.resolve(strict=True)
    builder = Path(__file__).resolve(strict=True)
    if any(path.stat().st_nlink != 1 for path in (uv, base_python, builder)):
        print(
            "Source wheel cache miss: shared-build-identity; retaining isolated source builds",
            file=sys.stderr, flush=True,
        )
        yield inputs
        return
    elapsed = {}
    with _source_phase("preparation", timeout, elapsed) as progress:
        cache_arguments = [
            argument for option, value in _argument_pairs(transaction.argv[3:])
            if option == "--cache-dir" for argument in (option, value)
        ]
        cache = run(
            [str(uv), "cache", "dir", *cache_arguments],
            environment=transaction.environment, cwd=transaction.cwd,
        ).stdout.strip()
        if len(cache) > 32_768 or not Path(cache).is_absolute():
            raise WheelError("native UV did not identify an absolute cache directory")
    with _temporary_inputs(None) as temporary:
        workspace = _private_retained_root(Path(temporary)).resolve()
        cutoffs = [
            argument for option, value in _argument_pairs(transaction.argv[3:])
            if option in ("--exclude-newer", "--exclude-newer-package")
            for argument in (option, value)
        ]
        environment = dict(transaction.environment)
        # Resolution is finished. Build hooks cannot consult another UV config,
        # index or interpreter selection through the source-build subprocess.
        # Request custody stays with the constructor, not the offline builder.
        environment = {key: value for key, value in environment.items()
                       if not key.upper().startswith(("UV_", "PIP_", "PYTHON"))
                       and key.upper() not in (
                           "VIRTUAL_ENV", "CONDA_PREFIX", "UPDATE_DRIVER_WITNESS",
                       )}
        environment.update({
            "UV_NO_CONFIG": "1", "UV_OFFLINE": "1", "UV_CACHE_DIR": cache,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        tool_sets = {}
        tools_seconds = 0.0

        def acquire_tools(requirements):
            nonlocal tools_seconds
            tools_started = time.monotonic()
            tooling = workspace / ("tools-" + str(len(acquired)))
            run(
                [str(base_python), "-I", "-m", "venv", "--without-pip", "--copies", str(tooling)],
                environment=transaction.environment, cwd=transaction.cwd,
            )
            python = tooling / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            with retain_uv_install(
                str(uv), ["--python", str(python), "--only-binary", ":all:", "--link-mode", "copy",
                          *requirements, *cutoffs],
                env=transaction.environment, cwd=transaction.cwd, deadline=_source_deadline(progress),
            ) as admitted:
                run(list(admitted.argv), environment=admitted.environment, cwd=admitted.cwd)
            observed = run(
                [str(python), "-I", "-c",
                 "import json,sys,sysconfig;print(json.dumps(dict("
                 "version=list(sys.version_info),implementation=sys.implementation.name,"
                 "platform=sysconfig.get_platform(),purelib=sysconfig.get_path('purelib'))))"],
                environment=transaction.environment, cwd=workspace,
            )
            target = json.loads(observed.stdout)
            tools_root = Path(target.pop("purelib"))
            if not tools_root.is_relative_to(tooling):
                raise WheelError("build tools are outside their private environment")
            tools_seconds += time.monotonic() - tools_started
            return python, tools_root, target

        built = 0
        archives, records = {}, {}
        selected = tuple(package for package, data in zip(inputs.packages, manifests) if (
            inputs.local_packages is None
            or re.sub(r"[-_.]+", "-", data["project"]["name"]).lower() in inputs.local_packages
        ))
        requirements_by_package = {
            package: tuple(sorted(set(manifests[inputs.packages.index(package)]["build-system"]["requires"])))
            for package in selected
        }
        requirement_groups = dict.fromkeys(requirements_by_package.values())
        acquired = {}
        with _source_phase("acquisition", timeout, elapsed) as progress:
            for requirements in requirement_groups:
                acquired[requirements] = acquire_tools(requirements)

        try:
            with _source_phase("offline", timeout, elapsed) as progress:
                identity = {
                    "schema": "installer-engine.source-build", "version": 1,
                    "python": _digest_file(base_python.resolve(strict=True), 256 << 20, progress),
                    "uv": _digest_file(uv, 256 << 20, progress),
                    "builder": _digest_file(builder, 1 << 20, progress),
                    # Job ancestry reaches children but does not identify build inputs.
                    "environment": hashlib.sha256(_canonical({
                        key: value for key, value in environment.items()
                        if key.upper() != "COPILOT_EXTENSIONS_OWNED_JOB_SCOPES"
                    })).hexdigest(),
                }

                def build_tools(requirements):
                    nonlocal tools_seconds
                    tools_started = time.monotonic()
                    python, tools_root, target = acquired[requirements]
                    tool_content = _tool_tree(tools_root, progress)
                    tools_seconds += time.monotonic() - tools_started
                    return python, tools_root, tool_content, {**identity, "target": target, "tools": tool_content}

                for requirements in requirement_groups:
                    tool_sets[requirements] = build_tools(requirements)
                for index, package in enumerate(selected):
                    requirements = requirements_by_package[package]
                    python, tools_root, tool_content, common = tool_sets[requirements]
                    compact = workspace / ("source-" + str(index))
                    source = _tree(package, progress, excluded={
                        path.relative_to(package).as_posix() for path in inputs.packages
                        if path != package and path.is_relative_to(package)
                    })
                    name = re.sub(r"[-_.]+", "-", manifests[inputs.packages.index(package)]["project"]["name"]).lower()

                    def verify_source():
                        if _tree(compact, progress) != source or _tool_tree(tools_root, progress) != tool_content:
                            raise WheelError("source or installed build tools changed before wheel cache publication")

                    def build(output):
                        nonlocal built
                        if _compact(package, inputs.packages, compact, progress) != source:
                            raise WheelError("source changed before its wheel build")
                        reported = run(
                            [str(python), "-I", "-B", "-c", _BACKEND_REQUIREMENTS],
                            environment=environment, cwd=compact,
                        ).stdout
                        if len(reported) > 65_536:
                            raise WheelError("backend build requirements exceed their byte budget")
                        requirements = json.loads(reported)
                        if not isinstance(requirements, list) or any(
                            not isinstance(value, str) or not value for value in requirements
                        ):
                            raise WheelError("backend reported invalid build requirements")
                        if requirements:
                            raise _AdditionalBuildRequirements
                        uv_output = workspace / ("build-" + str(index))
                        run(
                            [str(uv), "build", "--wheel", "--no-build-isolation", "--no-sources",
                             "--offline", "--python", str(python), "--out-dir", str(uv_output), str(compact)],
                            environment=environment, cwd=compact,
                        )
                        wheels = tuple(uv_output.glob("*.whl"))
                        if len(wheels) != 1:
                            raise WheelError("source build did not produce exactly one wheel")
                        target = output / wheels[0].name
                        shutil.copyfile(wheels[0], target)
                        built += 1
                        return target

                    def validate(path):
                        _wheel_record(path, name, progress)
                        verify_source()

                    archive = cached_source_wheel(
                        root, _canonical({**common, "source": source}), None,
                        build=build, validate=validate, max_archive_bytes=_LIMITS.max_archive_bytes,
                        deadline=progress.deadline, progress_deadline=lambda: progress.deadline,
                    )
                    records[name] = _wheel_record(archive, name, progress)
                    archives[package] = archive
                for _python, tools_root, tool_content, _common in tool_sets.values():
                    if _tool_tree(tools_root, progress) != tool_content:
                        raise WheelError("installed build tools changed during source construction")
                arguments = []
                selected_manifests = {package / "pyproject.toml" for package in selected}
                for option, value in _argument_pairs(transaction.argv[3:]):
                    if option in ("-r", "--requirements") and Path(value) in selected_manifests:
                        continue
                    if option:
                        arguments.extend((option, value) if value else (option,))
                        continue
                    for package, archive in archives.items():
                        suffix = f"[{','.join(inputs.extras)}]" if package == inputs.plugin and inputs.extras else ""
                        if value == str(package) + suffix:
                            name = re.sub(r"[-_.]+", "-", tomllib.loads(
                                (package / "pyproject.toml").read_text(encoding="utf-8"),
                            )["project"]["name"]).lower()
                            value = name + suffix + " @ " + archive.as_uri() + "#" + records[name]["sha256"].replace(":", "=", 1)
                            break
                    arguments.append(value)
                wheels = SourceWheelInputs(_canonical(records), tuple(archives.values()), timeout)
                wheels.verify(deadline=progress)
                progress.renew()
                assembled = replace(
                    inputs, transaction=transaction._replace(argv=(*transaction.argv[:3], *arguments)),
                    source_wheels=wheels,
                )
        except _AdditionalBuildRequirements:
            print(
                "Source wheel cache miss: backend-additional-build-requirements; "
                "retaining isolated source builds", file=sys.stderr, flush=True,
            )
            yield inputs
            return
        print(
            f"Source wheel assembly: built={built} reused={len(selected) - built} "
            f"tools={tools_seconds:.3f}s acquisition={elapsed['acquisition']:.3f}s "
            f"offline={elapsed['offline']:.3f}s total={time.monotonic() - started:.3f}s",
            file=sys.stderr, flush=True,
        )
        yield assembled
