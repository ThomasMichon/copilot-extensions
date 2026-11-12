"""Prepare native dependency inputs for an explicitly selected Manager source.

These are construction inputs, not release approval or runtime activation.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext, redirect_stdout
import hashlib
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time
import zipfile

from packaging.utils import canonicalize_name
from packaging.version import Version

SCRIPTS = Path(__file__).resolve().parents[1] / "worktree-manager" / "scripts"
sys.path.insert(0, str(SCRIPTS))
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "libs" / "installer-engine"))
import install_source as source_inputs
from installer_wheel_inputs import WheelLimits, _check_deadline, inspect_archive
from installer_wheel_snapshot import retain_wheel_inputs

from build_toolchain_lock import _query_marker_environment
from application_sources import application_sources
from promotion_python_artifacts import (
    PromotionArtifactError, project_key, validate_manifest, validate_wheel_sources,
)
from runtime_profile import SCHEMA, ProfileError, _reachable, _requirement, validate_profile
from runtime_profile_wheels import profile_wheel_inputs
from runtime_source_lock import _wheel_pins, dependencies
from standalone_python_artifacts import embedded_projects


def wheel_limits() -> WheelLimits:
    return WheelLimits(
        max_archive_bytes=128 << 20, max_expanded_bytes=1 << 30,
        max_member_bytes=512 << 20, max_metadata_bytes=16 << 20,
        max_members=100_000, max_tags=10_000, max_metadata_member_bytes=4 << 20,
    )


def registry_roots(wheels: dict, root: str, environment: dict, *, deadline: float) -> list[str]:
    """Project external roots from the actual applicable first-party metadata."""
    selected: dict[str, set[str]] = {}
    registry = set()
    pending = [(_requirement(root, deadline), {""}, {})]
    while pending:
        _check_deadline(deadline)
        requirement, parent_extras, sources = pending.pop()
        if requirement.marker and not any(
            requirement.marker.evaluate({**environment, "extra": extra})
            for extra in parent_extras
        ):
            continue
        name = canonicalize_name(requirement.name)
        if name in sources and (
            name not in wheels or wheels[name].get("source_project") != sources[name]
        ):
            raise ProfileError(f"{name}: selected associated application source is missing or different")
        if name not in wheels:
            requirement.marker = None
            registry.add(str(requirement))
            continue
        wheel = wheels[name]
        if not requirement.specifier.contains(wheel["version"], prereleases=True):
            raise ProfileError(f"{name}: first-party version violates its dependency")
        extras = {canonicalize_name(extra) for extra in requirement.extras}
        if not extras <= {canonicalize_name(extra) for extra in wheel["provides_extras"]}:
            raise ProfileError(f"{name}: first-party dependency requests an unknown extra")
        previous = selected.get(name)
        if previous is not None and extras <= previous:
            continue
        selected[name] = extras | (previous or set())
        pending.extend(
            (_requirement(text, deadline), {"", *selected[name]}, wheel.get("application_sources", {}))
            for text in wheel["requires_dist"]
        )
    return sorted(registry)


def _associated_first_party(
    snapshot: Path, *, deadline: float, component: str = "worktree-manager",
) -> tuple[source_inputs.ProjectSource, dict]:
    """Inspect the associated wheel bytes without constructing an environment."""
    selected = source_inputs.select_project(snapshot, component)
    project = selected.project
    manifest = project["artifact"]
    validate_manifest(manifest)
    if manifest.get("component", manifest.get("plugin")) != component:
        raise ProfileError("source wheel manifest has a foreign component")
    entries = manifest["wheels"]
    declared = {}
    package_roots = {}
    for package in source_inputs.engine._package_roots(selected.payload):
        metadata = dependencies._toml(package / "pyproject.toml")["project"]
        name = canonicalize_name(metadata["name"])
        if name in declared:
            raise ProfileError("selected source has ambiguous first-party names")
        declared[name] = Version(metadata["version"])
        package_roots[name] = package.resolve()
    embedded = embedded_projects(selected.payload) if component == "worktree-manager" else {}
    if any(
        name not in package_roots or directory.resolve() != package_roots[name]
        for name, (directory, _metadata) in embedded.items()
    ):
        raise ProfileError("embedded first-party sources differ from the selected source closure")
    metadata = dependencies._toml(selected.payload / "pyproject.toml")
    application = canonicalize_name(metadata["project"]["name"])
    try:
        sources = application_sources(metadata)
    except ValueError as exc:
        raise ProfileError(str(exc)) from exc
    archives = {
        row["filename"]: source_inputs._member(snapshot, project["wheels"] + "/" + row["filename"])
        for row in entries
    }
    expected = {row["filename"]: row["sha256"] for row in entries}
    if len(archives) != len(entries):
        raise ProfileError("associated source wheel filenames collide")
    limits = wheel_limits()
    result = {}
    with retain_wheel_inputs(
        archives, expected, max_wheels=256, max_archive_bytes=128 << 20,
        max_total_bytes=512 << 20, max_snapshot_bytes=1 << 30, deadline=deadline,
    ) as retained:
        expanded = 0
        for row in entries:
            facts = inspect_archive(retained.archives[row["filename"]], limits=limits, deadline=deadline)
            name = canonicalize_name(facts.name)
            expanded += facts.unpacked_size
            if expanded > limits.max_expanded_bytes or name in result:
                raise ProfileError("associated source wheels exceed their closure budget")
            if (
                name != canonicalize_name(row["name"])
                or name not in declared or Version(facts.version) != declared[name]
            ):
                raise ProfileError("associated source wheel name/version differs from selected source")
            if embedded and name == application:
                with zipfile.ZipFile(retained.archives[row["filename"]]) as archive:
                    validate_wheel_sources(
                        archive, facts.member_hashes, source_dir=selected.payload,
                        prefix=facts.prefix, metadata=facts.metadata,
                        component=component, candidate_root=selected.payload.parent,
                    )
                _check_deadline(deadline)
            result[name] = {
                "name": name, "version": facts.version,
                "filename": row["filename"], "sha256": row["sha256"],
                "wheel_directory": project["wheels"],
                "requires_python": facts.requires_python,
                "requires_dist": list(facts.requires_dist),
                "provides_extras": list(facts.provides_extras),
            }
            if name == application:
                result[name].update(source_project=project_key(manifest), application_sources=sources)
        retained.verify()
    if result.keys() != declared.keys() - embedded.keys():
        raise ProfileError("associated wheels do not complete the declared first-party source set")
    if source_inputs.select_project(snapshot, component).identity != selected.identity:
        raise ProfileError("selected source changed during wheel inspection")
    return selected, result


def first_party(
    snapshot: Path, *, deadline: float, component: str = "worktree-manager",
) -> tuple[source_inputs.ProjectSource, dict]:
    """Follow application dependencies through this snapshot's associated wheels."""
    selected, result = _associated_first_party(snapshot, deadline=deadline, component=component)
    applications = {}
    for key, project in selected.record["projects"].items():
        _check_deadline(deadline)
        if not isinstance(project, dict) or "source" not in project:
            continue
        payload = source_inputs._member(snapshot, project["payload"])
        metadata = dependencies._toml(payload / "pyproject.toml")["project"]
        name = canonicalize_name(metadata["name"])
        owner = key.removeprefix("standalone:")
        if name in applications and applications[name] != owner:
            raise ProfileError("source snapshot has ambiguous application distributions")
        applications[name] = owner
    pending = list(result.values())
    while pending:
        wheel = pending.pop()
        for text in wheel["requires_dist"]:
            name = canonicalize_name(_requirement(text, deadline).name)
            if name in result or name not in applications:
                continue
            dependency_source, associated = _associated_first_party(
                snapshot, deadline=deadline, component=applications[name],
            )
            for dependency, row in associated.items():
                previous = result.get(dependency)
                if previous is not None:
                    if any(previous[field] != row[field] for field in (
                        "version", "requires_python", "requires_dist", "provides_extras",
                    )):
                        raise ProfileError("associated first-party dependency declarations differ")
                    continue
                result[dependency] = row
                pending.append(row)
    return selected, result


def complete_profile(
    selected, target: dict, roots: list[str], retained, *,
    policy_id: str, acquisition_id: str, deadline: float,
):
    """Compose inspected metadata with the existing exact-closure validator."""
    wheels = {}
    expanded = 0
    limits = wheel_limits()
    for filename, archive in retained.archives.items():
        facts = inspect_archive(
            archive, limits=limits, deadline=deadline,
            record_algorithms=frozenset({"sha256", "sha384", "sha512"}),
        )
        name = canonicalize_name(facts.name)
        expanded += facts.unpacked_size
        if expanded > limits.max_expanded_bytes or name in wheels:
            raise ProfileError("native wheel closure exceeds its inspection budget")
        wheel = {
            "name": name, "version": facts.version, "filename": filename,
            "sha256": retained.digests[filename], "requires_python": facts.requires_python,
            "provides_extras": list(facts.provides_extras), "selected_extras": [],
            "requires_dist": list(facts.requires_dist),
        }
        wheels[name] = wheel, [_requirement(text, deadline) for text in facts.requires_dist]
    wanted = _reachable(wheels, roots, target["environment"], deadline)
    for name, extras in wanted.items():
        wheels[name][0]["selected_extras"] = sorted(extras)
    return validate_profile({
        "schema": SCHEMA, "version": 1, "mode": "source", "artifact": None,
        "release": {
            "identity": "source:" + selected.identity.source_commit,
            "sha256": "sha256:" + selected.identity.assembly_hash,
        },
        "component": selected.identity.plugin, "backend": "cpu", "target": target,
        "roots": roots, "approval_source": policy_id, "acquisition_policy": acquisition_id,
        "wheels": [wheels[name][0] for name in sorted(wanted)],
    }, deadline=deadline)


def _write_snapshot(
    selected, output: Path, profile, retained, *, deadline: float, uv: str | None = None,
) -> Path:
    prepared = output / "snapshot"
    if prepared.exists() or prepared.is_symlink():
        raise FileExistsError("prepared source snapshot already exists")
    component = selected.identity.plugin
    relative = "source/worktree-manager" if component == "worktree-manager" else selected.project["payload"]
    payload = prepared / relative
    packages = source_inputs.engine._package_roots(selected.payload)
    common = Path(os.path.commonpath(packages))
    layout = selected.payload.relative_to(common)
    destination = payload.parents[len(layout.parts) - 1] if layout.parts else payload
    frozen = source_inputs.engine.freeze_inputs(selected.payload, destination, deadline=deadline)
    if frozen.plugin != payload:
        raise ProfileError("prepared project payload does not match its selected layout")
    if source_inputs.sources.read_source_identity(
        frozen.plugin, selected.identity.package_version, plugin=component,
    ) != selected.identity:
        raise ProfileError("prepared project source differs from its selected identity")
    wheels = prepared / "wheels" / component
    wheels.mkdir(parents=True)
    for filename, archive in retained.archives.items():
        _check_deadline(deadline)
        archive.seek(0)
        with (wheels / filename).open("xb") as destination:
            shutil.copyfileobj(archive, destination, 1 << 20)
    profile_path = prepared / "profiles" / (component + ".json")
    profile_path.parent.mkdir()
    profile_path.write_bytes(profile.canonical)
    record = json.loads(json.dumps(selected.record))
    record["projects"] = {selected.project_key: record["projects"][selected.project_key]}
    project = record["projects"][selected.project_key]
    project.update({
        "payload": frozen.plugin.relative_to(prepared).as_posix(),
        "wheels": wheels.relative_to(prepared).as_posix(),
        "wheel_inputs": wheels.relative_to(prepared).as_posix(),
        "profile": profile_path.relative_to(prepared).as_posix(),
    })
    (prepared / source_inputs.MANIFEST).write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    if uv is not None:
        from runtime_application_project import emit, install_callbacks

        metadata = dependencies._toml(frozen.plugin / "pyproject.toml")["project"]
        profile_record = profile.record()
        environment = profile_record["target"]["environment"]
        major, minor = environment["python_full_version"].split(".")[:2]
        application = prepared / "applications" / component
        commands = metadata.get("scripts", {})
        primary = metadata["name"] if metadata["name"] in commands else next(iter(commands), "")
        roots = profile_record["roots"]
        root_names = {
            canonicalize_name(_requirement(requirement, deadline).name) for requirement in roots
        }
        with source_inputs.project_inputs(prepared, component, deadline=deadline) as (observed, inputs):
            construction = source_inputs.application_inputs(observed, inputs)
        setup, start = install_callbacks(primary, extras=tuple(construction["extras"]))
        emit(
            application, distribution=metadata["name"], version=metadata["version"],
            requires_python=f">={environment['python_full_version']},<{major}.{int(minor) + 1}",
            commands=commands, setup=setup, start=start,
            wheels={row["name"]: wheels / row["filename"] for row in profile_record["wheels"]},
            runtime_source=selected.identity.document(), target_environment=environment,
            target_tags=profile_record["target"]["tags"],
            construction=construction,
            requirements=[
                *roots,
                *(row["name"] + "==" + row["version"] for row in profile_record["wheels"]
                  if row["name"] not in root_names),
            ],
        )
        isolated = {
            key: value for key, value in os.environ.items()
            if key not in {
                "UV_PROJECT", "UV_PROJECT_ENVIRONMENT", "UV_NO_SYNC", "UV_NO_PROJECT",
                "UV_WORKING_DIR", "UV_FROZEN", "PYTHONPATH", "PYTHONHOME", "VIRTUAL_ENV",
            }
        }
        result = subprocess.run(
            [uv, "lock", "--offline", "--python", sys.executable],
            cwd=application, env=isolated, capture_output=True, text=True,
            timeout=max(0.1, deadline - time.monotonic()),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            raise ProfileError(f"application uv lock failed: {result.stderr}")
        project["application"] = application.relative_to(prepared).as_posix()
    (prepared / source_inputs.MANIFEST).write_text(
        json.dumps(record, sort_keys=True, indent=2) + "\n", encoding="utf-8",
    )
    with source_inputs.project_inputs(prepared, component, deadline=deadline) as (observed, inputs):
        if observed.identity != selected.identity:
            raise ProfileError("prepared snapshot source changed during readback")
        inputs.verify()
    return prepared


def _profile_policy(selected, environment: dict, download_origins: tuple[str, ...]):
    from governed_feed_trust import _opaque_index_identity, _validated_trusted_index_url

    trusted = _validated_trusted_index_url(environment)
    if trusted is None:
        raise ProfileError("native profile acquisition needs a configured, affirmatively trusted governed feed")
    return trusted, {
        "policyId": "sha256:" + _opaque_index_identity(json.dumps({
            "source": selected.identity.document(), "index": trusted,
            "downloadOrigins": download_origins,
        }, sort_keys=True)),
        "selection": {"downloadOrigins": list(download_origins)},
    }


def _profile_feed(
    selected, transaction, projections, *, uv: str, download_origins: tuple[str, ...],
    wheels, deadline: float, excluded_local_packages: tuple[str, ...] = (),
):
    from governed_feed_trust import _opaque_index_identity
    from installer_native_feed import capture_native_feed_inputs

    trusted, policy = _profile_policy(selected, transaction.environment, download_origins)
    feed = capture_native_feed_inputs(
        transaction, projections[1], projections[0], None, policy, uv, deadline,
        wheels=wheels, profile_policy="native-source-profile",
        opaque_identity=_opaque_index_identity, excluded_local_packages=excluded_local_packages,
    )
    if (
        {item.index_url for item in feed.feeds} != {trusted[0]}
        or len({item.identity for item in feed.feeds}) != 1
    ):
        raise ProfileError("native profile indexes differ from the selected governed feed")
    return policy, feed


def _prepare_profile(
    snapshot, selected, wheels, record, output, transaction, projections, *,
    uv: str, download_origins: tuple[str, ...], deadline: float,
) -> Path:
    from installer_wheel_acquisition import AcquisitionLimits, acquire_wheel_inputs

    policy, feed = _profile_feed(
        selected, transaction, projections, uv=uv, download_origins=download_origins,
        wheels=record["files"], deadline=deadline,
    )
    limits = AcquisitionLimits(
        max_packages=256, max_requests=2048, max_redirects=128,
        max_candidates_per_project=100_000, max_index_bytes=16 << 20,
        max_total_metadata_bytes=128 << 20, max_wheel_bytes=128 << 20,
        max_closure_bytes=512 << 20, max_temporary_bytes=1 << 30,
    )
    packages = set(source_inputs.engine._package_roots(selected.payload))
    associated = {row.get("wheel_directory") for row in wheels.values()}
    for project in selected.record["projects"].values():
        if project.get("wheels") in associated:
            packages.update(source_inputs.engine._package_roots(
                source_inputs._member(snapshot, project["payload"]),
            ))
    acquisition = acquire_wheel_inputs(
        record["files"], feeds=feed.feeds, download_origins=feed.download_origins,
        cutoffs=feed.cutoffs, policy_id=feed.policy_id, limits=limits, deadline=deadline,
        ssl_context=feed.context, proxies=feed.proxies,
        procutil=source_inputs.engine._procutil(source_inputs.engine.FrozenInputs(
            selected.payload, tuple(sorted(packages)),
            selected.identity.assembly_hash,
        )),
    ) if record["files"] else nullcontext(None)
    with acquisition as acquired:
        project = selected.project
        archives = dict(acquired.snapshot.paths) if acquired is not None else {}
        archives.update({
            wheel["filename"]: source_inputs._member(
                snapshot, wheel.get("wheel_directory", project["wheels"]) + "/" + wheel["filename"],
            )
            for wheel in wheels.values()
        })
        expected = {item["filename"]: item["sha256"] for item in record["files"]}
        expected.update({item["filename"]: item["sha256"] for item in wheels.values()})
        if len(archives) != len(record["files"]) + len(wheels):
            raise ProfileError("registry and associated first-party wheel filenames collide")
        with retain_wheel_inputs(
            archives, expected, max_wheels=256, max_archive_bytes=128 << 20,
            max_total_bytes=512 << 20, max_snapshot_bytes=1 << 30, deadline=deadline,
        ) as retained:
            profile = complete_profile(
                selected, record["target"], record["roots"], retained,
                policy_id=policy["policyId"], acquisition_id=feed.policy_id, deadline=deadline,
            )
            runtime = retained.subset(tuple(item["filename"] for item in profile.record()["wheels"]))
            inputs = profile_wheel_inputs(
                profile, runtime, max_archive_bytes=512 << 20, max_unpacked_bytes=1 << 30,
                max_metadata_bytes=4 << 20, limits=wheel_limits(), deadline=deadline,
            )
            inputs.verify()
            prepared = _write_snapshot(selected, output, profile, retained, deadline=deadline, uv=uv)
            inputs.verify()
            if acquired is not None:
                (output / "acquisition.json").write_bytes(acquired.evidence_json)
            return prepared


def prepare_registry_lock(
    snapshot: Path, output: Path, *, uv: str, deadline: float,
    prepare_profile: bool = False, download_origins: tuple[str, ...] = (),
    component: str = "worktree-manager", extras: tuple[str, ...] = (),
) -> dict:
    """Use the existing native compiler; never modify source or wheel metadata."""
    selected, wheels = first_party(snapshot, deadline=deadline, component=component)
    extras = source_inputs.engine._selected_extras(selected.payload, extras)
    environment = _query_marker_environment(Path(sys.executable), deadline=deadline)
    from packaging.tags import sys_tags

    target = {"environment": environment, "tags": [str(tag) for tag in sys_tags()], "device": "cpu"}
    name = source_inputs.engine._package_name(selected.payload)
    if name not in wheels:
        raise ProfileError("associated closure has no selected project wheel")
    extra_selection = "[" + ",".join(extras) + "]" if extras else ""
    root = f"{name}{extra_selection}=={wheels[name]['version']}"
    requirements = registry_roots(wheels, root, environment, deadline=deadline)
    output.mkdir(parents=True, exist_ok=False)
    requirements_file = output / "registry.in"
    requirements_file.write_text("\n".join(requirements) + "\n", encoding="utf-8")
    native = output / "pylock.toml"
    projections = []
    with dependencies.retain_uv_compile(
        uv, [
            str(requirements_file), "--python", sys.executable,
            "--python-version", environment["python_full_version"],
            "--only-binary", ":all:", "--no-python-downloads",
            "--format", "pylock.toml", "--output-file", str(native),
            *(["--system-certs"] if prepare_profile else []),
        ], cwd=output, env=dict(os.environ), deadline=deadline, native_target=True,
        _projection=projections,
    ) as transaction:
        if prepare_profile:
            _profile_feed(
                selected, transaction, projections, uv=uv, download_origins=download_origins,
                wheels=None, deadline=deadline,
            )
        result = subprocess.run(
            list(transaction.argv), cwd=transaction.cwd, env=transaction.environment,
            capture_output=True, text=True, timeout=deadline - time.monotonic(),
            creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0,
        )
        if result.returncode:
            raise ProfileError(f"native registry resolution failed: {result.stderr.strip()}")
        _check_deadline(deadline)
        with native.open("rb") as stream:
            content = stream.read((4 << 20) + 1)
        if len(content) > 4 << 20:
            raise ProfileError("native registry lock exceeds its document budget")
        files = _wheel_pins(content, target, deadline, prefer_target_tags=True)
        record = {
            "phase": "registry-locked", "install_ready": False,
            "source": selected.identity.document(), "target": target,
            "roots": [root], "registry_roots": requirements, "files": files,
            "first_party": wheels,
        }
        if prepare_profile:
            prepared = _prepare_profile(
                snapshot, selected, wheels, record, output, transaction, projections,
                uv=uv, download_origins=download_origins, deadline=deadline,
            )
            record.update({
                "phase": "profile-prepared", "install_ready": True,
                "snapshot": str(prepared),
            })
    if source_inputs.select_project(snapshot, component).identity != selected.identity:
        raise ProfileError("selected source changed during native resolution")
    (output / "registry-lock.json").write_text(json.dumps(record, sort_keys=True), encoding="utf-8")
    return record


def _application_selection(components: list[str], extras: dict):
    import prepare_runtime_source as preparation

    if (
        not isinstance(components, list) or not components or len(components) > 64
        or any(not isinstance(name, str) or not preparation._PLUGIN.fullmatch(name)
               for name in components)
        or len(set(components)) != len(components)
        or not isinstance(extras, dict) or set(extras) - set(components)
        or any(
            not isinstance(values, list) or len(values) > 64
            or any(not isinstance(value, str) or not preparation._PLUGIN.fullmatch(value)
                   for value in values)
            or len(set(values)) != len(values)
            for values in extras.values()
        )
    ):
        raise ProfileError("application profiles require bounded unique components and extras")


def _verify_application(snapshot: Path, component: str, record: dict, selected, inputs):
    from installer_engine_health import verify_application_wheels

    if (
        selected.identity.document() != record["projects"][selected.project_key]["source"]
        or selected.identity.source_commit != record["source_commit"]
        or selected.identity.source_tree != record["source_tree"]
        or selected.composition != record.get("composition")
        or selected.source_descriptor != record.get("source_descriptor")
    ):
        raise ProfileError("prepared application differs from the selected source")
    application = source_inputs._member(snapshot, selected.project.get("application"))
    declaration = source_inputs._read(application / "application.json")
    if (
        declaration.get("construction") != source_inputs.application_inputs(selected, inputs)
        or not source_inputs._member(
            snapshot, selected.project["application"] + "/uv.lock",
        ).is_file()
    ):
        raise ProfileError("prepared application has no matching locked construction inputs")
    try:
        verify_application_wheels(inputs, application)
    except (OSError, RuntimeError) as error:
        raise ProfileError("prepared application wheel copies differ from their retained inputs") from error


def prepare_applications(
    snapshot: Path, output: Path, components: list[str], *,
    extras: dict, uv: str, deadline: float, source_record_sha256: str,
) -> dict[str, str]:
    """Keep complete-profile inspection inside the existing dependency toolchain."""
    import prepare_runtime_source as preparation
    from standalone_python_artifacts import COMPONENTS

    preparation._remaining(deadline)
    _application_selection(components, extras)
    record = source_inputs._read(snapshot / source_inputs.MANIFEST)

    def verify_record():
        current = source_inputs._read(snapshot / source_inputs.MANIFEST)
        if hashlib.sha256(json.dumps(current, sort_keys=True).encode("utf-8")).hexdigest() != source_record_sha256:
            raise ProfileError("application profile source record differs from the selected source")

    verify_record()
    projects = {
        name: "worktree-manager" if name == "worktree-manager"
        else "standalone:" + name if name in COMPONENTS else name
        for name in components
    }
    snapshots = {}
    for component in components:
        preparation._remaining(deadline)
        profile_output = output / component
        prepared = prepare_registry_lock(
            snapshot, profile_output, uv=uv, deadline=deadline, prepare_profile=True,
            component=component, extras=tuple(extras.get(component, ())),
        )
        path = profile_output / "snapshot"
        if (
            prepared.get("phase") != "profile-prepared" or prepared.get("install_ready") is not True
            or prepared.get("snapshot") != str(path)
        ):
            raise ProfileError("application profile did not complete its selected snapshot")
        if prepared.get("source") != record["projects"][projects[component]]["source"]:
            raise ProfileError("prepared application differs from the selected source")
        snapshots[component] = str(path)
    for component, path in snapshots.items():
        prepared_snapshot = Path(path)
        with source_inputs.project_inputs(prepared_snapshot, component, deadline=deadline) as (selected, inputs):
            _verify_application(prepared_snapshot, component, record, selected, inputs)
            inputs.verify()
    verify_record()
    preparation._remaining(deadline)
    return snapshots


def reuse_applications(
    snapshot: Path, previous: Path, components: list[str], *, extras: dict,
    deadline: float, source_record_sha256: str, toolchain_record: dict,
) -> dict[str, str]:
    from packaging.tags import sys_tags
    from build_toolchain_lock import ArtifactBuildError
    from local_install_source import _application_completion, _reuse_projects, _source_projects
    from runtime_profile import ProfileUnavailable
    from standalone_python_artifacts import COMPONENTS

    _application_selection(components, extras)
    record = source_inputs._read(snapshot / source_inputs.MANIFEST)

    def verify_record():
        current = source_inputs._read(snapshot / source_inputs.MANIFEST)
        if hashlib.sha256(json.dumps(current, sort_keys=True).encode("utf-8")).hexdigest() != source_record_sha256:
            raise ProfileError("application profile source record differs from the selected source")

    verify_record()
    projects = tuple("standalone:" + name if name in COMPONENTS else name for name in components)
    roster, selected, payloads, identities = _source_projects(snapshot / "source", record, projects)
    try:
        completion = source_inputs._read(source_inputs._member(previous, "applications.json"))
        expected = {name: str(previous / "profiles" / name / "snapshot") for name in components}
        if completion.get("snapshots") != expected:
            raise ProfileError("prior completion does not name this selected portfolio")
        if _application_completion(expected, deadline=deadline) != completion:
            raise ProfileError("prior complete application outputs changed")
        manifests = _reuse_projects(
            source_inputs._member(previous, "inputs"), snapshot, record, selected, identities, roster,
            source_policy="governed", min_age_days=None, copy_wheels=False, deadline=deadline,
        )
        if any(manifest["build_toolchain"] != toolchain_record for manifest in manifests.values()):
            raise ProfileError("prior wheels differ from the retained toolchain")
        target = {
            "environment": _query_marker_environment(Path(sys.executable), deadline=deadline),
            "tags": [str(tag) for tag in sys_tags()], "device": "cpu",
        }
        old_record = source_inputs._read(previous / "inputs" / source_inputs.MANIFEST)
        for component, path in expected.items():
            prepared = Path(path)
            with source_inputs.project_inputs(prepared, component, deadline=deadline) as (observed, inputs):
                if observed.identity != identities[
                    "standalone:" + component if component in COMPONENTS else component
                ] or observed.project["artifact"] != old_record["projects"][observed.project_key]["artifact"]:
                    raise ProfileError("prior application differs from its selected source/wheels")
                profile = validate_profile(inputs.receipt()["binding"], deadline=deadline)
                profile_record = profile.record()
                selected_extras = source_inputs.engine._selected_extras(
                    observed.payload, tuple(extras.get(component, ())),
                )
                name = source_inputs.engine._package_name(observed.payload)
                root = name + ("[" + ",".join(selected_extras) + "]" if selected_extras else "")
                root += "==" + inputs.receipt()["distributions"][name]["version"]
                _trusted, policy = _profile_policy(observed, dict(os.environ), ())
                if (
                    profile_record["target"] != target or profile_record["roots"] != [root]
                    or profile_record["approval_source"] != policy["policyId"]
                ):
                    raise ProfileError("prior application target, extras or source policy changed")
                checked = profile_wheel_inputs(
                    profile, inputs.snapshot, max_archive_bytes=512 << 20,
                    max_unpacked_bytes=1 << 30, max_metadata_bytes=4 << 20,
                    limits=wheel_limits(), deadline=deadline,
                )
                _verify_application(prepared, component, old_record, observed, inputs)
                checked.verify()
                inputs.verify()
        if (
            _application_completion(expected, deadline=deadline) != completion
            or source_inputs._read(previous / "applications.json") != completion
        ):
            raise ProfileError("prior completion changed during application reuse")
    except (OSError, ValueError, KeyError, TypeError, PromotionArtifactError, ArtifactBuildError) as error:
        raise ProfileUnavailable(f"prior complete application outputs are not reusable: {error}") from error
    finally:
        verify_record()
        if any(source_inputs.sources.assembly_hash(payloads[key]) != identity.assembly_hash
               for key, identity in identities.items()):
            raise ProfileError("selected source changed during application reuse")
        _check_deadline(deadline)
    return expected


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--install-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", required=True)
    budget = parser.add_mutually_exclusive_group()
    budget.add_argument("--timeout", type=float, default=300)
    budget.add_argument("--deadline", type=float)
    parser.add_argument("--prepare-profile", action="store_true")
    parser.add_argument(
        "--download-origin", action="append", default=[],
        help="Optional exact HTTPS artifact-origin restriction; default follows configured-feed delivery",
    )
    selection = parser.add_mutually_exclusive_group()
    selection.add_argument("--component", default="worktree-manager")
    selection.add_argument("--application-components", type=json.loads)
    parser.add_argument("--application-extras", type=json.loads)
    parser.add_argument("--target-environment", type=json.loads)
    parser.add_argument("--source-record-sha256")
    parser.add_argument("--reuse-applications", type=Path)
    parser.add_argument("--toolchain-record", type=json.loads)
    parser.add_argument("--extra", action="append", default=[])
    args = parser.parse_args(argv)
    deadline = args.deadline if args.deadline is not None else time.monotonic() + args.timeout
    _check_deadline(deadline)
    if args.application_components is not None:
        if (
            args.prepare_profile or args.extra or args.download_origin
            or args.target_environment is None or args.source_record_sha256 is None
        ):
            parser.error("application batch requires an explicit target and no single-profile options")
        if _query_marker_environment(Path(sys.executable), deadline=deadline) != args.target_environment:
            if args.reuse_applications is not None:
                print("prior toolchain differs from the selected native target", file=sys.stderr)
                return 3
            raise ProfileError("profile tooling interpreter differs from the selected native target")
        with redirect_stdout(sys.stderr):
            if args.reuse_applications is not None:
                from runtime_profile import ProfileUnavailable

                if not isinstance(args.toolchain_record, dict):
                    parser.error("application reuse requires its retained toolchain record")
                try:
                    snapshots = reuse_applications(
                        args.install_source, args.reuse_applications, args.application_components,
                        extras={} if args.application_extras is None else args.application_extras,
                        deadline=deadline, source_record_sha256=args.source_record_sha256,
                        toolchain_record=args.toolchain_record,
                    )
                except ProfileUnavailable as error:
                    print(str(error), file=sys.stderr)
                    return 3
            else:
                if args.toolchain_record is not None:
                    parser.error("a retained toolchain record requires application reuse")
                snapshots = prepare_applications(
                    args.install_source, args.output.absolute(), args.application_components,
                    extras={} if args.application_extras is None else args.application_extras,
                    uv=args.uv, deadline=deadline, source_record_sha256=args.source_record_sha256,
                )
        print(json.dumps(snapshots, sort_keys=True))
        return 0
    if (
        args.application_extras is not None or args.target_environment is not None
        or args.source_record_sha256 is not None or args.reuse_applications is not None
        or args.toolchain_record is not None
    ):
        parser.error("application extras and target require --application-components")
    record = prepare_registry_lock(
        args.install_source, args.output.absolute(), uv=args.uv,
        deadline=deadline,
        prepare_profile=args.prepare_profile, download_origins=tuple(args.download_origin),
        component=args.component,
        extras=tuple(args.extra),
    )
    print(json.dumps({
        "phase": record["phase"], "install_ready": record["install_ready"],
        "registry_wheels": len(record["files"]), "snapshot": record.get("snapshot"),
    }))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
