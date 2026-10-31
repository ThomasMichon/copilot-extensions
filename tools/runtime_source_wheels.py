"""Complete a retained source lock with built first-party and admitted wheels.

The invoking installation owner retains source-lock custody and supplies acquired
third-party bytes. This module does not acquire packages, approve a feed, publish
reuse evidence or activate a runtime. Its output feeds the existing closed-wheel
constructor while both contexts remain open through installed health.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass, replace
import hashlib
import json
from pathlib import Path
import shutil
import stat
import time

from packaging.requirements import Requirement
from packaging.utils import canonicalize_name
from packaging.version import Version

from artifact_source_build import PromotionArtifactError, validate_runtime_metadata
from build_python_artifacts import build_wheel
from build_toolchain_lock import (
    ToolchainLock, _query_marker_environment,
    sanitize_subprocess_env, strip_package_source_env_vars,
)
from runtime_profile import SCHEMA, ProfileError, RuntimeProfile, _wheel_filename, validate_profile
from runtime_profile_wheels import profile_wheel_inputs
from runtime_source_lock import SourceLockCandidate
from installer_source_wheel_cache import cached_source_wheel
from installer_engine import _package_roots, _source_files, freeze_inputs, numbered_candidate, runtime
from installer_dependency_policy import _private_retained_root, _select_uv_executable, _temporary_inputs, _toml
from installer_wheel_inputs import WheelLimits, _check_deadline, _ordinary, inspect_archive
from installer_wheel_snapshot import (
    WheelInstallInputs, _digest as snapshot_digest, _open, _unchanged, retain_wheel_inputs,
)


@dataclass(frozen=True)
class SourceProfileInputs:
    profile: RuntimeProfile
    wheels: WheelInstallInputs
    local_packages: tuple[str, ...]


def _selected_projects(projects, root, extras, environment, deadline):
    manifests = {canonicalize_name(_toml(path / "pyproject.toml")["project"]["name"]):
                 (path, _toml(path / "pyproject.toml")["project"]) for path in projects}
    pending = [Requirement(root + ("[" + ",".join(extras) + "]" if extras else ""))]
    selected = {}
    while pending:
        _check_deadline(deadline)
        requirement = pending.pop()
        name = canonicalize_name(requirement.name)
        if name not in manifests:
            continue
        requested = {canonicalize_name(extra) for extra in requirement.extras}
        if name in selected and requested <= selected[name]:
            continue
        selected[name] = selected.get(name, set()) | requested
        _path, project = manifests[name]
        optional = {canonicalize_name(key): value for key, value in
                    project.get("optional-dependencies", {}).items()}
        if not requested <= optional.keys():
            raise ProfileError("source dependency requests an undeclared extra")
        dependencies = [*project.get("dependencies", [])]
        for extra in selected[name]:
            dependencies.extend(optional[extra])
        for text in dependencies:
            _check_deadline(deadline)
            edge = Requirement(text)
            if not edge.marker or any(edge.marker.evaluate({**environment, "extra": extra})
                                      for extra in {"", *selected[name]}):
                pending.append(edge)
    return tuple(manifests[name][0] for name in sorted(selected))


def _digest(path: Path, limit: int, deadline: float) -> str:
    _check_deadline(deadline)
    before = _ordinary(path)
    if before.st_size > limit:
        raise ProfileError("source wheel exceeds the archive byte budget")
    with _open(path) as stream:
        opened = _unchanged(path, before, stream)
        digest, size = snapshot_digest(stream, limit, deadline)
        _unchanged(path, before, stream, opened=opened)
    if size != before.st_size:
        raise ProfileError("source wheel changed while hashing")
    return digest


def _build_environment_identity(opaque_identity) -> str:
    environment = sanitize_subprocess_env()
    strip_package_source_env_vars(environment, strip_credentials=True)
    return opaque_identity(json.dumps(
        environment, sort_keys=True, separators=(",", ":"), ensure_ascii=True,
    ))


def _build_cache_binding(record, build_record, toolchain, uv, *, deadline, opaque_identity):
    """Only the admitted offline lane has exact backend archive identities."""
    tools = Path(__file__).parent
    files = [
        Path(uv).resolve(strict=True), toolchain.venv_python.resolve(strict=True),
        *(tools / name for name in (
            "runtime_source_wheels.py", "build_python_artifacts.py",
            "build_toolchain_lock.py", "artifact_source_build.py",
        )),
    ]
    return {
        "schema": "copilot-extensions.source-wheel-construction", "version": 1,
        # Retention remaps local paths into a fresh private workspace. Its raw
        # hash is custody evidence, not a stable identity across transactions.
        "source_sha256": record["source"]["assembly_hash"],
        "source_tree": record["source"]["source_tree"],
        "target": record["target"], "extras": record["extras"],
        "build_wheels": sorted(
            ({key: item[key] for key in ("name", "version", "filename", "sha256")}
             for item in build_record["wheels"]),
            key=lambda item: item["name"],
        ),
        "executors": [_digest(path, 256 << 20, deadline) for path in files],
        "environment": _build_environment_identity(opaque_identity),
    }


def _validate_local_metadata(facts, declaration, toolchain, *, admitted_build):
    generators = facts.wheel_metadata.get_all("Generator", [])
    if len(generators) != 1 or (not admitted_build and generators != [toolchain.generator]):
        raise ProfileError("built first-party wheel differs from its selected toolchain")
    try:
        validate_runtime_metadata(declaration, facts.metadata)
    except PromotionArtifactError as error:
        raise ProfileError("built first-party wheel differs from retained source: " + str(error)) from error


def _extras(wheels: dict[str, dict], roots: list[str], environment: dict, deadline: float) -> dict:
    wanted: dict[str, set[str]] = {}
    pending = [(Requirement(text), {""}) for text in roots]
    while pending:
        _check_deadline(deadline)
        requirement, parent_extras = pending.pop()
        if requirement.url:
            raise ProfileError("source wheel closure cannot contain URL/path dependencies")
        if requirement.marker and not any(
            requirement.marker.evaluate({**environment, "extra": extra}) for extra in parent_extras
        ):
            continue
        name = canonicalize_name(requirement.name)
        if name not in wheels:
            raise ProfileError(f"source wheel closure is incomplete: {name}")
        requested = {canonicalize_name(extra) for extra in requirement.extras}
        if name in wanted and requested <= wanted[name]:
            continue
        wanted[name] = wanted.get(name, set()) | requested
        for text in wheels[name]["requires_dist"]:
            _check_deadline(deadline)
            pending.append((Requirement(text), {"", *wanted[name]}))
    return wanted


@contextmanager
def retain_source_profile(
    candidate: SourceLockCandidate, *, third_party: dict[str, Path],
    uv: str, toolchain: ToolchainLock, limits: WheelLimits,
    max_wheels: int, max_total_bytes: int, max_snapshot_bytes: int, deadline: float,
    build_profile: RuntimeProfile | None = None, offline: bool = False,
    procutil=None, construction_fd: int | None = None,
    retained_runtime: tuple[Path, str, Path] | None = None,
    opaque_identity=None,
):
    """Build and freeze the exact complete source closure before construction.

    Third-party inputs must already be acquired under the candidate's retained
    policy. Every supplied byte must match that lock; there is no resolution,
    missing-wheel fallback or self-declared approval flag at this boundary.
    """
    _check_deadline(deadline)
    if not isinstance(candidate, SourceLockCandidate) or not isinstance(toolchain, ToolchainLock):
        raise ProfileError("source completion needs retained selection and a locked build toolchain")
    if any(type(value) is not int or value <= 0 for value in (
        max_wheels, max_total_bytes, max_snapshot_bytes,
    )):
        raise ProfileError("source completion requires positive closure budgets")
    record = candidate.record()
    build_record = None
    if build_profile is not None:
        if not isinstance(build_profile, RuntimeProfile) or not offline:
            raise ProfileError("admitted build-only inputs require owned offline construction")
        build_record = validate_profile(build_profile.record(), deadline=deadline).record()
        if (
            build_record["mode"] != "source"
            or toolchain.packages != {
                item["name"]: item["version"] for item in build_record["wheels"]
            }
            or build_record["target"] != record["target"]
            or build_record["component"] != record["component"]
            or build_record["roots"] != record.get("build_roots")
            or build_record["release"] != {
                "identity": "source:" + record["source"]["source_commit"],
                "sha256": "sha256:" + record["source"]["assembly_hash"],
            }
        ):
            raise ProfileError("build-only closure differs from the retained source selection")
    toolchain_record = json.loads(json.dumps(
        toolchain.record if build_record is None else {
            "packages": toolchain.packages, "admitted_profile": build_record,
        }, allow_nan=False,
    ))
    if "sha256:" + hashlib.sha256(candidate.requirements).hexdigest() != record["requirements_sha256"]:
        raise ProfileError("source candidate requirements changed")
    projects = _package_roots(candidate.payload)
    before = runtime.fingerprint_source(_source_files(projects))
    if "sha256:" + before != record["retained_source_sha256"]:
        raise ProfileError("retained source differs from the selected source lock")
    project = _toml(candidate.payload / "pyproject.toml")["project"]
    root = canonicalize_name(project["name"])
    selected_projects = _selected_projects(
        projects, root, record["extras"], record["target"]["environment"], deadline,
    )
    pins = {entry["name"]: entry for entry in record["pins"]}
    if len(selected_projects) + len(pins) > max_wheels:
        raise ProfileError("source closure exceeds the wheel count budget")
    third_party = dict(third_party)
    archives, expected, third_names = {}, {}, set()
    for filename, path in third_party.items():
        _check_deadline(deadline)
        name, version, _, _ = _wheel_filename(filename, limits.max_tags, deadline)
        digest = _digest(path, limits.max_archive_bytes, deadline)
        pin = pins.get(name)
        if (
            path.name != filename or name in third_names or pin is None
            or Version(pin["version"]) != version or digest not in pin["hashes"]
        ):
            raise ProfileError("third-party wheel differs from the selected source lock")
        third_names.add(name)
        archives[filename], expected[filename] = path, digest
    if third_names != pins.keys():
        raise ProfileError("third-party wheel set does not complete the source lock")
    if build_record is not None and any(
        item["name"] not in third_names
        or item["filename"] not in archives
        or expected[item["filename"]] != item["sha256"]
        or Version(pins[item["name"]]["version"]) != Version(item["version"])
        for item in build_record["wheels"]
    ):
        raise ProfileError("build-only closure differs from the retained source selection")
    if build_record is not None and not callable(opaque_identity):
        raise ProfileError("source wheel caching requires the installation owner's opaque identity provider")
    cache_binding = (
        _build_cache_binding(
            record, build_record, toolchain, uv, deadline=deadline, opaque_identity=opaque_identity,
        )
        if build_record is not None else None
    )
    with _temporary_inputs(None) as temporary:
        workspace = _private_retained_root(Path(temporary)).resolve(strict=True)
        output = workspace / "built"
        frozen = freeze_inputs(candidate.payload, workspace / "source", deadline=deadline) if offline else None
        if frozen is not None:
            if frozen.payload_hash != before:
                raise ProfileError("compact build source differs from its retained selection")
            selected_projects = _selected_projects(
                frozen.packages, root, record["extras"], record["target"]["environment"], deadline,
            )
        frozen_modes = (
            tuple((path, stat.S_IMODE(path.stat().st_mode)) for path in _source_files(frozen.packages))
            if frozen is not None else ()
        )

        def verify_compact_source():
            if frozen is not None:
                files = _source_files(frozen.packages)
                modes = tuple((path, stat.S_IMODE(path.stat().st_mode)) for path in files)
                if before != runtime.fingerprint_source(files) or modes != frozen_modes:
                    raise ProfileError("compact source changed before wheel cache publication")

        local_projects = {}
        for project_path in selected_projects:
            candidate.verify(deadline=deadline)
            verify_compact_source()
            declaration = _toml(project_path / "pyproject.toml")["project"]

            def build(destination):
                return build_wheel(
                    project_path, destination, toolchain=toolchain, reserved_names=set(archives),
                    uv=uv, deadline=deadline,
                    **({
                        "offline": True, "procutil": procutil,
                        "construction_fd": construction_fd, "retained_runtime": retained_runtime,
                    } if offline else {}),
                )

            def validate(wheel):
                with _open(wheel) as stream:
                    facts = inspect_archive(
                        stream, limits=limits, deadline=deadline,
                        record_algorithms=frozenset({"sha256"}),
                    )
                _validate_local_metadata(facts, declaration, toolchain, admitted_build=True)
                name, version, _, _ = _wheel_filename(wheel.name, limits.max_tags, deadline)
                if name != canonicalize_name(declaration["name"]) or version != Version(declaration["version"]):
                    raise ProfileError("built first-party wheel filename differs from retained source")
                if _build_environment_identity(opaque_identity) != cache_binding["environment"]:
                    raise ProfileError("source build environment changed during construction")
                verify_compact_source()
                candidate.verify(deadline=deadline)

            if cache_binding is None:
                wheel = build(output)
            else:
                binding = json.dumps({
                    **cache_binding, "project": canonicalize_name(declaration["name"]),
                }, sort_keys=True, separators=(",", ":"), allow_nan=False).encode("ascii")
                wheel = cached_source_wheel(
                    Path(record["policy"]["owner"]["runtimeRoot"]), binding, output,
                    build=build, validate=validate,
                    max_archive_bytes=limits.max_archive_bytes, deadline=deadline,
                )
            name, version, _, _ = _wheel_filename(wheel.name, limits.max_tags, deadline)
            if (
                name != canonicalize_name(declaration["name"])
                or version != Version(declaration["version"])
                or name in local_projects or name in pins
            ):
                raise ProfileError("built first-party wheel differs from its selected source/toolchain")
            local_projects[name] = declaration
            archives[wheel.name] = wheel
            expected[wheel.name] = _digest(wheel, limits.max_archive_bytes, deadline)
        if before != runtime.fingerprint_source(_source_files(projects)):
            raise ProfileError("source changed while building the exact wheel closure")
        verify_compact_source()
        observed_toolchain = toolchain.record if build_record is None else {
            "packages": toolchain.packages, "admitted_profile": build_profile.record(),
        }
        if observed_toolchain != toolchain_record:
            raise ProfileError("build toolchain selection changed while building the source closure")
        with retain_wheel_inputs(
            archives, expected, max_wheels=max_wheels,
            max_archive_bytes=limits.max_archive_bytes, max_total_bytes=max_total_bytes,
            max_snapshot_bytes=max_snapshot_bytes, deadline=deadline,
        ) as snapshot:
            wheels = {}
            expanded = 0
            for filename, stream in snapshot.archives.items():
                _check_deadline(deadline)
                facts = inspect_archive(
                    stream, limits=limits, deadline=deadline,
                    record_algorithms=(
                        frozenset({"sha256", "sha384", "sha512"})
                        if filename in third_party else frozenset({"sha256"})
                    ),
                )
                expanded += facts.unpacked_size
                if expanded > limits.max_expanded_bytes:
                    raise ProfileError("source closure exceeds the expanded byte budget")
                name = canonicalize_name(facts.name)
                if name in wheels:
                    raise ProfileError("source closure has duplicate distribution identities")
                if name in local_projects:
                    _validate_local_metadata(
                        facts, local_projects[name], toolchain, admitted_build=build_record is not None,
                    )
                wheels[name] = {
                    "name": name, "version": facts.version, "filename": filename,
                    "sha256": snapshot.digests[filename], "requires_python": facts.requires_python,
                    "provides_extras": list(facts.provides_extras), "selected_extras": [],
                    "requires_dist": list(facts.requires_dist),
                }
            extras = "[" + ",".join(record["extras"]) + "]" if record["extras"] else ""
            roots = [f"{root}{extras}=={project['version']}"]
            wanted = _extras(wheels, roots, record["target"]["environment"], deadline)
            build_names = {item["name"] for item in build_record["wheels"]} if build_record else set()
            if set(wheels) - wanted.keys() - build_names:
                raise ProfileError("source closure includes unreachable wheels")
            wheels = {name: wheels[name] for name in wanted}
            for name, selected in wanted.items():
                wheels[name]["selected_extras"] = sorted(selected)
            profile = validate_profile({
                "schema": SCHEMA, "version": 1, "mode": "source", "artifact": None,
                "release": {
                    "identity": "source:" + record["source"]["source_commit"],
                    "sha256": "sha256:" + record["source"]["assembly_hash"],
                },
                "component": record["component"], "backend": record["policy"]["selection"]["backend"],
                "target": record["target"], "roots": roots,
                "approval_source": record["policy"]["policyId"],
                "acquisition_policy": candidate.lock_id,
                "wheels": [wheels[name] for name in sorted(wheels)],
            }, max_tags=limits.max_tags, deadline=deadline)
            runtime_snapshot = snapshot.subset(tuple(
                item["filename"] for item in profile.record()["wheels"]
            )) if build_record is not None else snapshot
            inputs = profile_wheel_inputs(
                profile, runtime_snapshot, max_archive_bytes=max_total_bytes,
                max_unpacked_bytes=limits.max_expanded_bytes,
                max_metadata_bytes=limits.max_metadata_member_bytes,
                limits=limits, deadline=deadline,
            )
            inputs = replace(inputs, binding_json=json.dumps({
                "profile": profile.record(), "source_lock": record,
                "build_toolchain": toolchain_record,
                **({"build_profile": build_record} if build_record is not None else {}),
            }, sort_keys=True, separators=(",", ":"), ensure_ascii=True, allow_nan=False))
            inputs.verify()
            candidate.verify(deadline=deadline)
            yield SourceProfileInputs(profile, inputs, tuple(sorted(local_projects)))
            inputs.verify()
            candidate.verify(deadline=deadline)


@contextmanager
def source_candidate(
    candidate: SourceLockCandidate, *, third_party: dict[str, Path],
    uv: str, toolchain: ToolchainLock, health_module: str, limits: WheelLimits,
    max_wheels: int, max_total_bytes: int, max_snapshot_bytes: int, deadline: float,
):
    """Realize the frozen closure through the existing immutable constructor.

    This does not select a current generation or register/start a service.
    The authority keeps retain_source_lock open around this context.
    """
    record = candidate.record()
    root = Path(record["policy"]["owner"]["runtimeRoot"])
    python = Path(record["python"])
    if not root.is_absolute() or not python.is_absolute():
        raise ProfileError("source construction requires the selected owner's absolute runtime and interpreter")
    selected_uv = shutil.which(_select_uv_executable(uv))
    if selected_uv is None:
        raise ProfileError("source construction needs the selected UV executable")
    uv = str(Path(selected_uv).resolve(strict=True))
    with retain_source_profile(
        candidate, third_party=third_party, uv=uv, toolchain=toolchain, limits=limits,
        max_wheels=max_wheels, max_total_bytes=max_total_bytes,
        max_snapshot_bytes=max_snapshot_bytes, deadline=deadline,
    ) as selected:
        _check_deadline(deadline)
        if _query_marker_environment(python, deadline=deadline) != record["target"]["environment"]:
            raise ProfileError("construction interpreter differs from the selected target")
        _check_deadline(deadline)
        remaining = deadline - time.monotonic()

        def validate_candidate(slot):
            candidate.verify(deadline=deadline)
            installed_python = runtime.slot_python(root, slot.name)
            if _query_marker_environment(installed_python, deadline=deadline) != record["target"]["environment"]:
                raise ProfileError("installed interpreter differs from the selected target")

        with numbered_candidate(
            root, record["source"]["runtime_version"],
            plugin=candidate.payload, uv=Path(uv), base_python=python,
            health_module=health_module, extras=tuple(record["extras"]),
            local_packages=selected.local_packages,
            wheel_inputs=selected.wheels, wait_timeout=remaining, child_timeout=remaining,
            validate_candidate=validate_candidate,
        ) as built:
            yield built
