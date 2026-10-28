"""Content-bound local wheels for the ordinary retained-source constructor.

Native UV still admits build dependencies through the caller's retained settings.
The installed build tools, not an open requirement or a backend version alone,
identify subsequent offline builds. Unsupported build contracts retain the
ordinary isolated-build path.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import ast
import hashlib
import json
import os
from pathlib import Path
import re
import shutil
import stat
import sys
import time
import tomllib

from installer_dependency_policy import (
    _argument_pairs, _native_retained_path, _private_retained_root, _temporary_inputs,
    _write_toml, retain_uv_install,
)
from installer_source_wheel_cache import cached_source_wheel
from installer_wheel_inputs import WheelError, WheelLimits, _check_deadline, inspect_archive
from installer_wheel_snapshot import _digest, _open, _ordinary, _unchanged


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

    def verify(self) -> None:
        deadline = time.monotonic() + self.io_timeout
        records = {record["filename"]: record for record in self.receipt().values()}
        if {path.name for path in self.archives} != records.keys():
            raise WheelError("source wheel archives differ from their construction")
        for path in self.archives:
            if _digest_file(path, _LIMITS.max_archive_bytes, deadline) != records[path.name]["sha256"]:
                raise WheelError("retained source wheel changed during installation")

    def origins(self) -> dict:
        paths = {path.name: path for path in self.archives}
        return {
            name: paths[record["filename"]].as_uri() + "#"
            + record["sha256"].replace(":", "=", 1)
            for name, record in self.receipt().items()
        }


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()


def _digest_file(path: Path, limit: int, deadline: float) -> str:
    before = _ordinary(path)
    with _open(path) as stream:
        opened = _unchanged(path, before, stream)
        digest, _size = _digest(stream, limit, deadline)
        _unchanged(path, before, stream, opened=opened)
    return digest


def _ignored_source(relative: Path, excluded: set[str]) -> bool:
    name = relative.name
    return relative.as_posix() in excluded or name in {
        ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
        ".test-runs", "build", "dist", "node_modules",
    } or name.endswith((".egg-info", ".dist-info", ".pyc", ".pyo"))


def _manifest_digest(path: Path, limit: int, deadline: float) -> str:
    before = _ordinary(path)
    if before.st_size > limit:
        raise WheelError("source wheel manifest exceeds its byte budget")
    _check_deadline(deadline)
    with _open(path) as stream:
        opened = _unchanged(path, before, stream)
        content = stream.read(before.st_size + 1)
        _unchanged(path, before, stream, opened=opened)
    data = tomllib.loads(content.decode("utf-8"))
    data.get("tool", {}).get("uv", {}).pop("sources", None)
    return "sha256:" + hashlib.sha256(_canonical(data)).hexdigest()


def _tree(
    root: Path, deadline: float, *, installed: bool = False, excluded: set[str] | None = None,
) -> dict:
    excluded = excluded or set()
    result, total = {}, 0
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
            _check_deadline(deadline)
            path = Path(directory) / name
            relative = path.relative_to(root)
            if name.endswith((".pyc", ".pyo")) or (
                not installed and _ignored_source(relative, excluded)
            ):
                continue
            info = _ordinary(path)
            if len(result) >= _MAX_SOURCE_FILES or info.st_size > _MAX_SOURCE_BYTES - total:
                raise WheelError("source wheel input tree exceeds its byte or file budget")
            digest = (
                _manifest_digest(path, _MAX_SOURCE_BYTES - total, deadline)
                if not installed and relative == Path("pyproject.toml")
                else _digest_file(path, _MAX_SOURCE_BYTES - total, deadline)
            )
            result[relative.as_posix()] = (
                digest, stat.S_IMODE(info.st_mode),
            )
            total += info.st_size
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


def _compact(package: Path, packages: tuple[Path, ...], output: Path, deadline: float) -> dict:
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
            _digest(incoming, _MAX_SOURCE_BYTES - total, deadline, destination=target)
            _unchanged(path, before, incoming, opened=opened)
        shutil.copymode(source, destination)
        count += 1
        total += before.st_size
        return destination

    _check_deadline(deadline)
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


def _tool_tree(root: Path, deadline: float) -> dict:
    return _tree(root, deadline, installed=True)


def _wheel_record(path: Path, name: str, deadline: float) -> dict:
    with _open(path) as stream:
        facts = inspect_archive(stream, limits=_LIMITS, deadline=deadline)
    if (
        re.sub(r"[-_.]+", "-", facts.name).lower() != name or facts.tags != ("py3-none-any",)
        or facts.wheel_metadata["Root-Is-Purelib"] != "true"
        or any(".data/" in member for member in facts.member_hashes)
    ):
        raise WheelError("cached source build did not produce its ordinary pure-Python wheel")
    return {
        "filename": path.name, "version": facts.version,
        "sha256": _digest_file(path, _LIMITS.max_archive_bytes, deadline),
        "files": {
            member: digest for member, digest in facts.member_hashes.items()
            if member != facts.prefix + "RECORD"
        },
    }


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
    deadline = time.monotonic() + timeout
    runner = run

    def run(argv, **options):
        _check_deadline(deadline)
        return runner(argv, timeout=deadline - time.monotonic(), **options)

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
        environment = {key: value for key, value in environment.items()
                       if not key.upper().startswith(("UV_", "PIP_", "PYTHON"))
                       and key.upper() not in ("VIRTUAL_ENV", "CONDA_PREFIX")}
        environment.update({
            "UV_NO_CONFIG": "1", "UV_OFFLINE": "1", "UV_CACHE_DIR": cache,
            "PYTHONDONTWRITEBYTECODE": "1",
        })
        identity = {
            "schema": "installer-engine.source-build", "version": 1,
            "python": _digest_file(base_python.resolve(strict=True), 256 << 20, deadline),
            "uv": _digest_file(uv, 256 << 20, deadline),
            "builder": _digest_file(builder, 1 << 20, deadline),
            "environment": hashlib.sha256(_canonical(environment)).hexdigest(),
        }
        tool_sets = {}
        tools_seconds = 0.0

        def build_tools(requirements):
            nonlocal tools_seconds
            tools_started = time.monotonic()
            tooling = workspace / ("tools-" + str(len(tool_sets)))
            run(
                [str(base_python), "-I", "-m", "venv", "--without-pip", "--copies", str(tooling)],
                environment=transaction.environment, cwd=transaction.cwd,
            )
            python = tooling / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
            with retain_uv_install(
                str(uv), ["--python", str(python), "--only-binary", ":all:", "--link-mode", "copy",
                          *requirements, *cutoffs],
                env=transaction.environment, cwd=transaction.cwd, deadline=deadline,
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
            tool_content = _tool_tree(tools_root, deadline)
            tools_seconds += time.monotonic() - tools_started
            return python, tools_root, tool_content, {**identity, "target": target, "tools": tool_content}

        built = 0
        archives, records = {}, {}
        selected = tuple(package for package, data in zip(inputs.packages, manifests) if (
            inputs.local_packages is None
            or re.sub(r"[-_.]+", "-", data["project"]["name"]).lower() in inputs.local_packages
        ))
        for index, package in enumerate(selected):
            manifest = manifests[inputs.packages.index(package)]
            requirements = tuple(sorted(set(manifest["build-system"]["requires"])))
            if requirements not in tool_sets:
                tool_sets[requirements] = build_tools(requirements)
            python, tools_root, tool_content, common = tool_sets[requirements]
            compact = workspace / ("source-" + str(index))
            source = _tree(package, deadline, excluded={
                path.relative_to(package).as_posix() for path in inputs.packages
                if path != package and path.is_relative_to(package)
            })
            name = re.sub(r"[-_.]+", "-", manifest["project"]["name"]).lower()

            def verify_source():
                if _tree(compact, deadline) != source or _tool_tree(tools_root, deadline) != tool_content:
                    raise WheelError("source or installed build tools changed before wheel cache publication")

            def build(output):
                nonlocal built
                if _compact(package, inputs.packages, compact, deadline) != source:
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
                _wheel_record(path, name, deadline)
                verify_source()

            try:
                archive = cached_source_wheel(
                    root, _canonical({**common, "source": source}), None,
                    build=build, validate=validate, max_archive_bytes=_LIMITS.max_archive_bytes,
                    deadline=deadline,
                )
            except _AdditionalBuildRequirements:
                print(
                    "Source wheel cache miss: backend-additional-build-requirements; "
                    "retaining isolated source builds", file=sys.stderr, flush=True,
                )
                yield inputs
                return
            records[name] = _wheel_record(archive, name, deadline)
            archives[package] = archive
        for _python, tools_root, tool_content, _common in tool_sets.values():
            if _tool_tree(tools_root, deadline) != tool_content:
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
        wheels.verify()
        print(
            f"Source wheel assembly: built={built} reused={len(selected) - built} "
            f"tools={tools_seconds:.3f}s total={time.monotonic() - started:.3f}s",
            file=sys.stderr, flush=True,
        )
        yield replace(
            inputs, transaction=transaction._replace(argv=(*transaction.argv[:3], *arguments)),
            source_wheels=wheels,
        )
