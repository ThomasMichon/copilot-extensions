"""Prepare native dependency inputs for an explicitly selected Manager source.

These are construction inputs, not release approval or runtime activation.
"""
from __future__ import annotations

import argparse
from contextlib import nullcontext
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import time

from packaging.utils import canonicalize_name
from packaging.version import Version

SCRIPTS = Path(__file__).resolve().parents[1] / "worktree-manager" / "scripts"
sys.path.insert(0, str(SCRIPTS))
import install_source as source_inputs
from installer_wheel_inputs import WheelLimits, _check_deadline, inspect_archive
from installer_wheel_snapshot import retain_wheel_inputs

from build_toolchain_lock import _query_marker_environment
from promotion_python_artifacts import validate_manifest
from runtime_profile import SCHEMA, ProfileError, _reachable, _requirement, validate_profile
from runtime_profile_wheels import profile_wheel_inputs
from runtime_source_lock import _wheel_pins, dependencies


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
    pending = [(_requirement(root, deadline), {""})]
    while pending:
        _check_deadline(deadline)
        requirement, parent_extras = pending.pop()
        if requirement.marker and not any(
            requirement.marker.evaluate({**environment, "extra": extra})
            for extra in parent_extras
        ):
            continue
        name = canonicalize_name(requirement.name)
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
            (_requirement(text, deadline), {"", *selected[name]})
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
    for package in source_inputs.engine._package_roots(selected.payload):
        metadata = dependencies._toml(package / "pyproject.toml")["project"]
        name = canonicalize_name(metadata["name"])
        if name in declared:
            raise ProfileError("selected source has ambiguous first-party names")
        declared[name] = Version(metadata["version"])
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
            result[name] = {
                "name": name, "version": facts.version,
                "filename": row["filename"], "sha256": row["sha256"],
                "wheel_directory": project["wheels"],
                "requires_python": facts.requires_python,
                "requires_dist": list(facts.requires_dist),
                "provides_extras": list(facts.provides_extras),
            }
        retained.verify()
    if source_inputs.select_project(snapshot, component).identity != selected.identity:
        raise ProfileError("selected source changed during wheel inspection")
    return selected, result


def first_party(
    snapshot: Path, *, deadline: float, component: str = "worktree-manager",
) -> tuple[source_inputs.ProjectSource, dict]:
    """Follow application dependencies through this snapshot's associated wheels."""
    selected, result = _associated_first_party(snapshot, deadline=deadline, component=component)
    inspected = [selected]
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
            inspected.append(dependency_source)
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
    declared = {
        canonicalize_name(dependencies._toml(package / "pyproject.toml")["project"]["name"])
        for project in inspected
        for package in source_inputs.engine._package_roots(project.payload)
    }
    if result.keys() != declared:
        raise ProfileError("associated wheels do not complete the declared first-party source set")
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
        setup, start = install_callbacks(primary)
        roots = profile_record["roots"]
        root_names = {
            canonicalize_name(_requirement(requirement, deadline).name) for requirement in roots
        }
        with source_inputs.project_inputs(prepared, component, deadline=deadline) as (observed, inputs):
            construction = source_inputs.application_inputs(observed, inputs)
        emit(
            application, distribution=metadata["name"], version=metadata["version"],
            requires_python=f">={environment['python_full_version']},<{major}.{int(minor) + 1}",
            commands=commands, setup=setup, start=start,
            wheels={row["name"]: wheels / row["filename"] for row in profile_record["wheels"]},
            runtime_source=selected.identity.document(), target_environment=environment,
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


def _profile_feed(
    selected, transaction, projections, *, uv: str, download_origins: tuple[str, ...],
    wheels, deadline: float,
):
    from governed_feed_trust import _opaque_index_identity, _validated_trusted_index_url
    from installer_native_feed import capture_native_feed_inputs
    trusted = _validated_trusted_index_url(transaction.environment)
    if trusted is None:
        raise ProfileError("native profile acquisition needs an affirmatively trusted governed feed")
    policy = {
        "policyId": "sha256:" + _opaque_index_identity(json.dumps({
            "source": selected.identity.document(), "index": trusted,
            "downloadOrigins": download_origins,
        }, sort_keys=True)),
        "selection": {"downloadOrigins": list(download_origins)},
    }
    feed = capture_native_feed_inputs(
        transaction, projections[1], projections[0], None, policy, uv, deadline,
        wheels=wheels, profile_policy="native-source-profile",
        opaque_identity=_opaque_index_identity,
    )
    if tuple(item.index_url for item in feed.feeds) != (trusted[0],):
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


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__, allow_abbrev=False)
    parser.add_argument("--install-source", type=Path, required=True)
    parser.add_argument("--output", type=Path, required=True)
    parser.add_argument("--uv", required=True)
    parser.add_argument("--timeout", type=float, default=300)
    parser.add_argument("--prepare-profile", action="store_true")
    parser.add_argument("--download-origin", action="append", default=[])
    parser.add_argument("--component", default="worktree-manager")
    parser.add_argument("--extra", action="append", default=[])
    args = parser.parse_args(argv)
    record = prepare_registry_lock(
        args.install_source, args.output.absolute(), uv=args.uv,
        deadline=time.monotonic() + args.timeout,
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
