"""Python-owned runtime construction, shared by independently shipped installers."""

from __future__ import annotations

import importlib.util
import hashlib
import json
import os
import re
import shutil
import stat
import subprocess
import sys
import time
from contextlib import ExitStack, contextmanager
from dataclasses import dataclass
from pathlib import Path
from types import ModuleType

import tomllib
import versioned_runtime as runtime
import versioned_runtime_source as runtime_source
from installer_dependency_policy import RetainedUVInstall, retain_uv_install
from installer_engine_process import ChildLaunchError, ChildTimeoutExpired, run_owned

_GENERATED_DIRECTORIES = {
    ".git", ".venv", "__pycache__", ".pytest_cache", ".ruff_cache",
    "build", "dist", "node_modules",
}


@dataclass(frozen=True)
class FrozenInputs:
    plugin: Path
    packages: tuple[Path, ...]
    payload_hash: str
    transaction: RetainedUVInstall | None = None
    retained_hash: str | None = None


@dataclass(frozen=True)
class ReadyCandidate:
    slot: Path
    payload_hash: str
    reused: bool
    descriptor: int
    frozen_plugin: Path


class HealthRepairRequired(RuntimeError):
    """Published health needs a distinct generation, never an in-place rebuild."""


def _package_roots(plugin: Path) -> tuple[Path, ...]:
    pending: list[tuple[Path, str | None]] = [(plugin, None)]
    roots: set[Path] = set()
    names: dict[str, Path] = {}
    while pending:
        declared, expected_name = pending.pop()
        if declared.is_symlink():
            raise ValueError(f"Runtime package input is a symlink: {declared}")
        root = declared.resolve(strict=True)
        manifest = root / "pyproject.toml"
        if not stat.S_ISREG(manifest.lstat().st_mode):
            raise ValueError(f"Runtime package manifest is not a regular file: {manifest}")
        data = tomllib.loads(manifest.read_text(encoding="utf-8"))
        name = data.get("project", {}).get("name")
        if not isinstance(name, str) or not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9._-]*", name):
            raise ValueError(f"Runtime package has no ordinary distribution name: {manifest}")
        key = re.sub(r"[-_.]+", "-", name).lower()
        if expected_name is not None and key != re.sub(r"[-_.]+", "-", expected_name).lower():
            raise ValueError(f"Runtime dependency {expected_name} resolves to {name}: {manifest}")
        if root in roots:
            continue
        if key in names and names[key] != root:
            raise ValueError(f"Runtime input declares two sources for {name}: {names[key]}, {root}")
        names[key] = root
        roots.add(root)
        sources = data.get("tool", {}).get("uv", {}).get("sources", {})
        if not isinstance(sources, dict):
            raise ValueError(f"Runtime package sources must be a table: {manifest}")
        for dependency, source in sources.items():
            if not isinstance(source, dict):
                raise ValueError(f"Unsupported runtime source for {dependency}: {manifest}")
            if "path" not in source:
                raise ValueError(f"Unsupported nonlocal runtime source for {dependency}: {manifest}")
            path = source["path"]
            if not isinstance(path, str) or not path:
                raise ValueError(f"Runtime dependency path is invalid for {dependency}: {manifest}")
            pending.append((root / path, dependency))
    return tuple(sorted(roots))


def _source_files(roots: tuple[Path, ...]) -> tuple[Path, ...]:
    files: set[Path] = set()

    def scan_error(error: OSError) -> None:
        raise error

    for root in roots:
        for directory, children, names in os.walk(root, onerror=scan_error, followlinks=False):
            parent = Path(directory)
            for name in list(children):
                path = parent / name
                mode = path.lstat().st_mode
                if not stat.S_ISDIR(mode):
                    raise ValueError(f"Runtime input directory is not ordinary: {path}")
                if name in _GENERATED_DIRECTORIES or name.endswith((".egg-info", ".dist-info")):
                    children.remove(name)
            for name in names:
                path = parent / name
                if not stat.S_ISREG(path.lstat().st_mode):
                    raise ValueError(f"Runtime input file is not ordinary: {path}")
                if not name.endswith((".pyc", ".pyo")):
                    files.add(path)
    return tuple(sorted(files))


def freeze_inputs(plugin: Path, destination: Path) -> FrozenInputs:
    """Copy and verify all local package inputs before admitting a numbered build.

    The caller owns a fresh destination outside the input trees. Relative package
    layout is preserved, so transitive ``tool.uv.sources`` paths remain attributable.
    Numbered construction consumes these copies non-editably, never a live checkout.
    """
    roots = _package_roots(plugin)
    plugin = plugin.resolve(strict=True)
    common = Path(os.path.commonpath(roots))
    destination = destination.resolve()
    for root in roots:
        if destination == root or destination.is_relative_to(root):
            raise ValueError(f"Frozen input destination is inside runtime source: {destination}")
    files = _source_files(roots)
    before = runtime.fingerprint_source(files)
    destination.mkdir(parents=True, exist_ok=False)
    try:
        for source in files:
            target = destination / source.relative_to(common)
            target.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(source, target, follow_symlinks=False)
            shutil.copymode(source, target, follow_symlinks=False)
        frozen_roots = tuple(destination / root.relative_to(common) for root in roots)
        frozen_plugin = destination / plugin.relative_to(common)
        if _package_roots(frozen_plugin) != tuple(sorted(frozen_roots)):
            raise RuntimeError("Runtime dependency graph changed while freezing inputs")
        after_files = _source_files(roots)
        if after_files != files:
            changed = sorted(set(after_files).symmetric_difference(files))
            detail = ", ".join(str(path.relative_to(common)) for path in changed[:10])
            raise RuntimeError(f"Runtime input file set changed while freezing inputs: {detail}")
        after = runtime.fingerprint_source(files)
        frozen_hash = runtime.fingerprint_source(_source_files(frozen_roots))
        if before != after or before != frozen_hash:
            raise RuntimeError("Runtime input content changed while freezing inputs")
        return FrozenInputs(frozen_plugin, frozen_roots, frozen_hash)
    except BaseException:
        shutil.rmtree(destination)
        raise


def _procutil(inputs: FrozenInputs) -> ModuleType:
    for root in inputs.packages:
        data = tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))
        if data["project"]["name"] == "agent-procutil":
            path = root / "src" / "agent_procutil" / "__init__.py"
            namespace = "_installer_procutil_" + hashlib.sha256(str(path).encode("utf-8")).hexdigest()
            spec = importlib.util.spec_from_file_location(namespace, path)
            if spec is None or spec.loader is None:
                raise ImportError(f"Cannot load frozen installer process ownership: {path}")
            module = importlib.util.module_from_spec(spec)
            sys.modules[spec.name] = module
            try:
                spec.loader.exec_module(module)
            except BaseException:
                sys.modules.pop(spec.name, None)
                raise
            return module
    raise ValueError("Runtime inputs do not declare the canonical agent-procutil dependency")


@contextmanager
def _retained_inputs(inputs: FrozenInputs, uv: Path, python: Path, *, refresh: bool):
    files = _source_files(inputs.packages)
    environment = dict(os.environ)
    for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
        environment.pop(name, None)
    with retain_uv_install(
        str(uv.resolve(strict=True)),
        ["--python", str(python), "--no-sources", "--no-editable", "--link-mode", "copy",
         *(["--reinstall"] if refresh else []), *map(str, inputs.packages)],
        env=environment, cwd=inputs.plugin,
        trusted_source_roots=inputs.packages,
    ) as transaction:
        if set(transaction.sources) != set(inputs.packages):
            raise RuntimeError("Retained UV inputs differ from the runtime dependency graph")
        if (
            _package_roots(inputs.plugin) != inputs.packages
            or _source_files(inputs.packages) != files
            or runtime.fingerprint_source(files) != inputs.payload_hash
        ):
            raise RuntimeError("Runtime inputs changed while retaining UV inputs")
        if not set(files) <= set(transaction.source_hashes):
            raise RuntimeError("Retained UV inputs do not cover the runtime source files")
        changed = [
            path for path in files
            if hashlib.sha256(path.read_bytes()).hexdigest() != transaction.source_hashes[path]
        ]
        if changed:
            raise RuntimeError(
                "Retained UV inputs differ from the admitted source content: "
                + ", ".join(str(path) for path in changed[:10])
            )
        packages = tuple(transaction.sources[path] for path in inputs.packages)
        plugin = transaction.sources[inputs.plugin]
        yield FrozenInputs(
            plugin, packages, inputs.payload_hash, transaction,
            runtime.fingerprint_source(_source_files(packages)),
        )


def _child(
    argv: list[str], *, inputs: FrozenInputs, descriptor: int,
    timeout: float, delays: tuple[float, ...] = (), transient: str = "",
    environment: dict[str, str] | None = None, cwd: Path | None = None,
) -> subprocess.CompletedProcess:
    env = dict(os.environ if environment is None else environment)
    if environment is None:
        for name in ("PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV"):
            env.pop(name, None)
    procutil = _procutil(inputs)
    for attempt in range(len(delays) + 1):
        try:
            return run_owned(
                argv, cwd=cwd or Path(Path(argv[0]).resolve().anchor), env=env, timeout=timeout,
                procutil=procutil, construction_fd=descriptor,
            )
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
            time.sleep(delays[attempt])
        except subprocess.TimeoutExpired as error:
            diagnostic = f"{error.stdout or ''}{error.stderr or ''}"
            print(
                f"Installer child timed out after {timeout}s:\n{diagnostic}",
                file=sys.stderr, flush=True,
            )
            raise
    raise AssertionError("installer retry loop did not settle")


def _input_origins(inputs: FrozenInputs) -> dict[str, str]:
    return {
        tomllib.loads((root / "pyproject.toml").read_text(encoding="utf-8"))["project"]["name"]:
        root.as_uri() for root in inputs.packages
    }


def _verify_installed(
    inputs: FrozenInputs, slot: Path, python: Path | None, module: str,
    descriptor: int, timeout: float, *, health_callable: str | None = None,
    require_frozen_origin: bool = True,
) -> None:
    if python is None:
        raise HealthRepairRequired(
            f"Runtime {slot.name} has no installed interpreter; "
            "use distinct-generation health repair, not an in-place rebuild."
        )
    origins = _input_origins(inputs)
    probe = """
import importlib, importlib.metadata, json, pathlib, sys
slot = pathlib.Path(sys.argv[1]).resolve()
module = importlib.import_module(sys.argv[2])
assert module.__file__ and pathlib.Path(module.__file__).resolve().is_relative_to(slot), \
    "health module is outside the candidate runtime"
assert sys.version_info >= (3, 11), "installer runtime requires Python 3.11+"
if sys.argv[4]:
    getattr(module, sys.argv[4])()
expected_origins = json.loads(sys.argv[3])
proof = slot / ".runtime-input-origins.json"
assert not proof.is_symlink(), "frozen origin evidence is a symlink"
if sys.argv[5] == "fresh":
    assert proof.is_file(), "fresh runtime has no frozen origin evidence"
recorded_origins = None
if proof.exists():
    receipt = json.loads(proof.read_text(encoding="utf-8"))
    assert receipt["schema"] == "installer-engine.frozen-input-origins" and \
        type(receipt["version"]) is int and receipt["version"] == 1, \
        "frozen origin evidence has an unsupported schema"
    recorded_origins = receipt["packages"]
    assert isinstance(recorded_origins, dict) and set(recorded_origins) == set(expected_origins), \
        "frozen origin evidence does not cover the dependency graph"
    if sys.argv[5] == "fresh":
        assert recorded_origins == expected_origins, "frozen origin evidence differs from construction"
for name, expected in expected_origins.items():
    distribution = importlib.metadata.distribution(name)
    assert pathlib.Path(distribution.locate_file("")).resolve().is_relative_to(slot), \
        f"{name} metadata is outside the candidate runtime"
    origin = json.loads(distribution.read_text("direct_url.json") or "{}")
    assert origin.get("dir_info") == {"editable": False} or \
        origin.get("dir_info") == {}, f"{name} is not a non-editable local installation"
    if sys.argv[5] == "fresh":
        assert origin.get("url") == expected, f"{name} was not installed from frozen input"
    elif recorded_origins is not None:
        assert origin.get("url") == recorded_origins[name], \
            f"{name} differs from its recorded frozen installation origin"
    else:
        staging = (slot.parent.parent / ".installer-inputs").as_uri() + "/frozen-"
        assert str(origin.get("url", "")).startswith(staging), \
            f"{name} has no attributable frozen installation origin"
"""
    try:
        _child(
            [str(python), "-I", "-B", "-c", probe, str(slot), module, json.dumps(origins),
             health_callable or "", "fresh" if require_frozen_origin else "reuse"],
            inputs=inputs, descriptor=descriptor, timeout=timeout,
        )
    except (subprocess.CalledProcessError, ChildLaunchError, ChildTimeoutExpired) as error:
        if require_frozen_origin:
            raise
        raise HealthRepairRequired(
            f"Published runtime {slot.name} failed isolated installed health; "
            f"use distinct-generation repair.\n{getattr(error, 'stderr', None) or error}"
        ) from error


def _construct(
    inputs: FrozenInputs, slot: Path, uv: Path, base_python: Path,
    descriptor: int, timeout: float, *, refresh: bool = False,
) -> Path:
    uv = uv.resolve(strict=True)
    base_python = base_python.resolve(strict=True)
    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
    if not refresh or not python.is_file() or not (slot / "pyvenv.cfg").is_file():
        _child(
            [str(base_python), "-I", "-m", "venv", "--without-pip", "--copies", str(slot)],
            inputs=inputs, descriptor=descriptor, timeout=timeout,
            delays=(0.75, 0.75) if os.name == "nt" else (),
            transient=r"Access is denied|failed to persist temporary file",
        )
    if not python.is_file() or not (slot / "pyvenv.cfg").is_file():
        raise RuntimeError(f"Runtime construction did not produce a complete interpreter: {slot}")
    transaction = inputs.transaction
    if transaction is None:
        raise RuntimeError("Runtime construction requires the canonical retained UV transaction")
    _child(
        list(transaction.argv),
        inputs=inputs, descriptor=descriptor, timeout=timeout,
        delays=(3, 6, 10), transient=r"SRE module mismatch",
        environment=transaction.environment, cwd=transaction.cwd,
    )
    if runtime.fingerprint_source(_source_files(inputs.packages)) != inputs.retained_hash:
        raise RuntimeError("Frozen runtime inputs changed during package construction")
    return python


def _prepare_installed(
    inputs: FrozenInputs, slot: Path, python: Path, callable_name: str,
    data: dict, descriptor: int, timeout: float,
) -> None:
    module, separator, function = callable_name.partition(":")
    if not separator or not module or not function:
        raise ValueError("Construction callable must be an installed module:function")
    probe = """
import importlib, json, pathlib, sys
slot = pathlib.Path(sys.argv[1]).resolve()
module = importlib.import_module(sys.argv[2])
assert module.__file__ and pathlib.Path(module.__file__).resolve().is_relative_to(slot), \
    "construction module is outside the candidate runtime"
getattr(module, sys.argv[3])(json.loads(sys.argv[4]))
"""
    _child(
        [str(python), "-I", "-B", "-c", probe, str(slot), module, function,
         json.dumps({**data, "frozen_plugin": str(inputs.plugin)})],
        inputs=inputs, descriptor=descriptor, timeout=timeout,
    )


def _refuse(decision: str, version: str) -> None:
    if decision == runtime.ADMIT_CONTENT_CONFLICT:
        raise RuntimeError(
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


def _assert_source_pin(plugin: Path, expected: dict | None) -> None:
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
    construction_callable: str | None = None, construction_data: dict | None = None,
    process_retention: bool = False,
    reuse_generations: bool = False,
    expected_source_identity: dict | None = None,
    wait_timeout: float = 180, child_timeout: float = 180,
):
    """Construct or reuse an immutable identity; lifecycle owns cutover/governance."""
    if version == "dev":
        raise ValueError("Mutable dev construction requires its explicit owning lifecycle")
    return _candidate(
        root, version, plugin=plugin, uv=uv, base_python=base_python,
        health_module=health_module, health_callable=health_callable,
        construction_callable=construction_callable, construction_data=construction_data,
        process_retention=process_retention, reuse_generations=reuse_generations,
        expected_source_identity=expected_source_identity,
        wait_timeout=wait_timeout, child_timeout=child_timeout,
    )


def dev_candidate(root: Path, *, owner: str, **kwargs):
    """Refresh only the explicitly claimed mutable slot under its outer lifecycle."""
    return _candidate(root, runtime.DEV_VERSION, dev_owner=owner, **kwargs)


def _check_dev_owner(root: Path, owner: str) -> None:
    with runtime._publication_lease(root):
        claim = runtime.read_dev_claim(root, strict=True)
        if claim is None or claim["owner"] != owner:
            raise RuntimeError("Mutable runtime construction requires this owner's explicit dev claim")


@contextmanager
def _candidate(
    root: Path, version: str, *, plugin: Path, uv: Path, base_python: Path,
    health_module: str, health_callable: str | None = None,
    construction_callable: str | None = None, construction_data: dict | None = None,
    process_retention: bool = False, dev_owner: str | None = None,
    reuse_generations: bool = False,
    expected_source_identity: dict | None = None,
    wait_timeout: float = 180, child_timeout: float = 180,
):
    root = root.resolve()
    _assert_source_pin(plugin, expected_source_identity)
    (root / ".installer-inputs").mkdir(parents=True, exist_ok=True)
    packages = _package_roots(plugin)
    inputs = FrozenInputs(
        plugin.resolve(strict=True), packages, runtime.fingerprint_source(_source_files(packages)),
    )
    with ExitStack() as retained:
        if dev_owner is not None:
            _check_dev_owner(root, dev_owner)
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
        decision = (
            runtime.ADMIT_CONSTRUCT if dev_owner is not None else
            runtime.check_admission(root, version, payload_hash=inputs.payload_hash)
        )
        _refuse(decision, version)
        if decision == runtime.ADMIT_REUSE:
            with runtime.retention_custody(
                root, version, payload_hash=inputs.payload_hash, timeout=wait_timeout,
            ) as (slot, descriptor):
                _assert_source_pin(plugin, expected_source_identity)
                _verify_installed(
                    inputs, slot, runtime.slot_python(root, version), health_module,
                    descriptor, child_timeout, health_callable=health_callable,
                    require_frozen_origin=False,
                )
                _assert_source_pin(plugin, expected_source_identity)
                yield ReadyCandidate(slot, inputs.payload_hash, True, descriptor, inputs.plugin)
            return
        with ExitStack() as construction:
            with runtime.construction_lease(root, version, timeout=wait_timeout) as descriptor:
                _assert_source_pin(plugin, expected_source_identity)
                if dev_owner is not None:
                    _check_dev_owner(root, dev_owner)
                decision = (
                    runtime.ADMIT_CONSTRUCT if dev_owner is not None else
                    runtime.check_admission(root, version, payload_hash=inputs.payload_hash)
                )
                _refuse(decision, version)
                reused = decision == runtime.ADMIT_REUSE
                if not reused:
                    slot = runtime.slot(root, version, clean_incomplete=dev_owner is None)
                    if dev_owner is not None:
                        runtime.marker_path(root, version).unlink(missing_ok=True)
                    python = slot / ("Scripts/python.exe" if os.name == "nt" else "bin/python")
                    inputs = retained.enter_context(_retained_inputs(
                        inputs, uv, python, refresh=dev_owner is not None,
                    ))
                    arguments = {"refresh": True} if dev_owner is not None else {}
                    python = _construct(inputs, slot, uv, base_python, descriptor, child_timeout, **arguments)
                    origin_proof = slot / ".runtime-input-origins.json"
                    if origin_proof.is_symlink():
                        raise RuntimeError("Frozen installation origin evidence cannot be a symlink")
                    origin_proof.write_text(json.dumps({
                        "schema": "installer-engine.frozen-input-origins", "version": 1,
                        "packages": _input_origins(inputs),
                    }, indent=2) + "\n", encoding="utf-8")
                    if process_retention:
                        bootstrap = slot / ".runtime-bootstrap"
                        if bootstrap.is_symlink():
                            raise RuntimeError("Runtime bootstrap cannot be a symlink")
                        bootstrap.mkdir(exist_ok=dev_owner is not None)
                        for name in (
                            "runtime_launcher.py", "versioned_runtime.py",
                            "versioned_runtime_leases.py", "versioned_runtime_source.py",
                            "versioned_runtime_construction.py",
                            "versioned_runtime_publication.py",
                        ):
                            source = inputs.plugin / "scripts" / name
                            shutil.copyfile(source, bootstrap / name)
                    if construction_callable is not None:
                        _prepare_installed(
                            inputs, slot, python, construction_callable,
                            construction_data or {}, descriptor, child_timeout,
                        )
                    _verify_installed(
                        inputs, slot, python, health_module, descriptor, child_timeout,
                        health_callable=health_callable,
                    )
                    _assert_source_pin(plugin, expected_source_identity)
                    if dev_owner is not None:
                        _check_dev_owner(root, dev_owner)
                    runtime.mark_complete(root, version, payload_hash=inputs.payload_hash)
                # Transfer to retention while construction is still owned. The
                # caller never joins cutover coordination holding construction.
                slot, retention_descriptor = construction.enter_context(
                    runtime.retention_custody(
                        root, version, payload_hash=inputs.payload_hash, timeout=wait_timeout,
                    )
                )
            if reused:
                _verify_installed(
                    inputs, slot, runtime.slot_python(root, version), health_module,
                    retention_descriptor, child_timeout, health_callable=health_callable,
                    require_frozen_origin=False,
                )
            _assert_source_pin(plugin, expected_source_identity)
            yield ReadyCandidate(slot, inputs.payload_hash, reused, retention_descriptor, inputs.plugin)
